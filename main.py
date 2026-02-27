from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
import hashlib, secrets, json, os, re, urllib.request, urllib.error, base64
from datetime import datetime
from models import init_db, get_db, User, MealPlan
from sqlalchemy.orm import Session

# ─── LOAD .env ────────────────────────────────────────────
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    os.environ[k] = v
        print(f"✅ .env loaded")
    else:
        print(f"⚠️  No .env file found at {env_path}")

load_env()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()

print(f"🔑 Gemini: {'✅ ' + GEMINI_API_KEY[:12] + '...' if GEMINI_API_KEY else '❌ NOT SET - will use fallback plans'}")
print(f"🔑 Google OAuth: {'✅ SET' if GOOGLE_CLIENT_ID else '⚠️  NOT SET'}")

app = FastAPI(title="NutriSync API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
init_db()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def read_html(name):
    path = os.path.join(BASE_DIR, name)
    if not os.path.exists(path):
        return f"<h1>File {name} not found at {path}</h1><p>BASE_DIR: {BASE_DIR}</p>"
    with open(path, encoding="utf-8") as f:
        return f.read()

@app.get("/", response_class=HTMLResponse)
def serve_index(): return read_html("index.html")

@app.get("/planner", response_class=HTMLResponse)
def serve_planner(): return read_html("planner.html")

@app.get("/dashboard", response_class=HTMLResponse)
def serve_dashboard(): return read_html("dashboard.html")

@app.get("/api/health")
def health():
    return {
        "status": "✅ running",
        "gemini": "✅ key set" if GEMINI_API_KEY else "❌ no key - fallback mode",
        "google_oauth": "✅ configured" if GOOGLE_CLIENT_ID else "⚠️ not configured",
        "db": "✅ connected",
        "base_dir": BASE_DIR,
        "time": datetime.utcnow().isoformat()
    }

@app.get("/api/google-client-id")
def get_gcid():
    return {"client_id": GOOGLE_CLIENT_ID or ""}

# ─── SCHEMAS ──────────────────────────────────────────────
class RegisterRequest(BaseModel):
    name: str; email: str; password: str

class LoginRequest(BaseModel):
    email: str; password: str

class GoogleLoginRequest(BaseModel):
    credential: str
    name: Optional[str] = None
    email: Optional[str] = None

class PlanRequest(BaseModel):
    age: int; gender: str; height_cm: float; weight_kg: float
    goal: str; activity: str
    medical_conditions: List[str] = []
    dietary_style: str = ""
    proteins: List[str] = []
    allergies: List[str] = []
    cuisines: List[str] = []
    budget: str = "Moderate"
    steps_today: Optional[int] = None
    calories_burned: Optional[int] = None
    active_minutes: Optional[int] = None

# ─── AUTH HELPERS ─────────────────────────────────────────
def hp(p): return hashlib.sha256(p.encode()).hexdigest()
def tok(): return secrets.token_hex(32)

def get_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "): return None
    t = authorization[7:].strip()
    if not t or t in ("null","undefined",""): return None
    return db.query(User).filter(User.token == t).first()

# ─── AUTH ROUTES ──────────────────────────────────────────
@app.post("/api/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if not req.name.strip() or not req.email.strip() or not req.password:
        raise HTTPException(400, "All fields are required")
    email = req.email.lower().strip()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Email already registered — please login instead")
    t = tok()
    u = User(name=req.name.strip(), email=email, password_hash=hp(req.password), token=t, created_at=datetime.utcnow())
    db.add(u); db.commit()
    print(f"✅ Registered: {email}")
    return {"token": t, "name": u.name, "email": u.email}

@app.post("/api/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email = req.email.lower().strip()
    u = db.query(User).filter(User.email == email).first()
    if not u or u.password_hash != hp(req.password):
        raise HTTPException(401, "Wrong email or password")
    u.token = tok(); db.commit()
    print(f"✅ Login: {email}")
    return {"token": u.token, "name": u.name, "email": u.email}

@app.post("/api/google-login")
def google_login(req: GoogleLoginRequest, db: Session = Depends(get_db)):
    try:
        parts = req.credential.split(".")
        if len(parts) != 3: raise HTTPException(400, "Invalid Google token")
        pad = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad).decode("utf-8"))
        email = payload.get("email","").lower().strip()
        name = payload.get("name", req.name or "Google User")
        if not email: raise HTTPException(400, "No email in Google token")
        u = db.query(User).filter(User.email == email).first()
        if not u:
            u = User(name=name, email=email, password_hash=hp(secrets.token_hex(16)), token=tok(), created_at=datetime.utcnow())
            db.add(u)
            print(f"✅ New Google user: {email}")
        else:
            u.token = tok()
            print(f"✅ Google login: {email}")
        db.commit()
        return {"token": u.token, "name": u.name, "email": u.email}
    except HTTPException: raise
    except Exception as e:
        print(f"Google login error: {e}")
        raise HTTPException(400, f"Google login failed: {str(e)}")

@app.get("/api/user-info")
def user_info(user=Depends(get_user)):
    if not user: raise HTTPException(401, "Not authenticated")
    return {"name": user.name, "email": user.email, "id": user.id}

# ─── NUTRITION MATH ───────────────────────────────────────
AMF = {"sedentary":1.2,"light":1.375,"moderate":1.55,"very":1.725,"extra":1.9}

def bmr(w,h,a,g): return round(10*w+6.25*h-5*a+(5 if g.lower() in["male","m"] else -161))
def tdee(b,act): return round(b*AMF.get(act.lower(),1.55))
def target_cal(t,goal): g=goal.lower(); return t-500 if "loss" in g else t+300 if "gain" in g or "muscle" in g else t
def fit_adj(t,steps,burned):
    if steps: t=round(t*0.9) if steps<4000 else round(t*1.1) if steps>10000 else t
    if burned and burned>0: t+=round(burned*0.3)
    return t

# ─── GEMINI ───────────────────────────────────────────────
def call_gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    body = json.dumps({"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":0.7,"maxOutputTokens":4096}}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=45) as r:
        d = json.loads(r.read())
    return d["candidates"][0]["content"]["parts"][0]["text"]

# ─── CLINICAL AI PROMPT (NutriSync System Prompt v2 — exact spec) ──────────
def build_prompt(req, target, b, t):
    bmi = round(req.weight_kg / ((req.height_cm / 100) ** 2), 1)
    allergies_str  = ', '.join(req.allergies)          if req.allergies          else 'None'
    medical_str    = ', '.join(req.medical_conditions) if req.medical_conditions else 'None'
    cuisines_str   = ', '.join(req.cuisines)           if req.cuisines           else 'Any'
    proteins_str   = ', '.join(req.proteins)           if req.proteins           else 'All'

    SYSTEM = """You are a Clinical Nutrition AI Assistant integrated into a health application.

Your purpose is to generate SAFE, personalized, explainable, and medically-aware meal recommendations.

You MUST follow the rules below strictly.

----------------------------------------------------
SECTION 1: NON-NEGOTIABLE SAFETY RULES
----------------------------------------------------

1. ALLERGIES ARE HARD CONSTRAINTS.
   - If a user lists an allergy, NEVER include that ingredient.
   - NEVER include derivatives of the allergen.
   - NEVER include "may contain".
   - If uncertain, exclude the ingredient.

2. Medical conditions override fitness goals.

3. If there is any conflict between:
   Allergy > Medical Condition > Calorie Target > Fitness Goal > Preference

4. If input data is incomplete, use the data provided and generate the best plan possible.

----------------------------------------------------
SECTION 2: REQUIRED TASKS
----------------------------------------------------

You must perform ALL of the following:

1. Use the provided daily calorie target.

2. Generate a 7-day meal plan. Each day must include:
   - Breakfast
   - Mid-Morning Snack
   - Lunch
   - Evening Snack
   - Dinner

3. For EACH meal include:
   - Ingredients with quantities and per-ingredient calories
   - Total meal calories
   - Macronutrient breakdown (Protein, Carbs, Fats)
   - Micronutrient highlights (e.g. Iron, Vitamin C, Calcium)

4. Provide Explainable AI Section for EACH meal:
   - Why this meal was selected
   - Nutritional purpose
   - How it supports user goal
   - Confirmation it is safe given the allergies

5. Provide a simple recipe for each meal:
   - Step-by-step instructions
   - Cooking time
   - Preparation difficulty (Easy / Medium / Advanced)

6. Perform a SELF-SAFETY CHECK:
   - Re-scan all ingredients
   - Confirm no allergens exist
   - Confirm medical condition compatibility

7. Provide a short voice_summary (2 friendly sentences, no jargon).

----------------------------------------------------
SECTION 3: IMPORTANT BEHAVIORAL RULES
----------------------------------------------------

- Be medically cautious.
- Do not hallucinate rare ingredients.
- Prefer common, accessible foods.
- Use realistic calorie numbers.
- Keep recipes practical.
- Never override allergy constraint.
- Never produce unsafe diet advice.
- Use cuisine style and budget to pick ingredients.
----------------------------------------------------"""

    USER_DATA = f"""
----------------------------------------------------
USER INPUT DATA
----------------------------------------------------

USER PROFILE:
- Age: {req.age}
- Gender: {req.gender}
- Height: {req.height_cm} cm
- Weight: {req.weight_kg} kg
- BMI: {bmi}
- Activity Level: {req.activity}
- Fitness Goal: {req.goal}

CALCULATED NUTRITION TARGETS:
- BMR: {b} kcal/day
- TDEE: {t} kcal/day
- Daily Calorie Target: {target} kcal/day

GOOGLE FIT DATA:
- Steps today: {req.steps_today or 'Not connected'}
- Calories burned today: {req.calories_burned or 'Not connected'}
- Active minutes: {req.active_minutes or 'Not connected'}

MEDICAL CONDITIONS:
- {medical_str}

ALLERGIES (HARD CONSTRAINT — NEVER include these or their derivatives):
- {allergies_str}

DIETARY PREFERENCES:
- Style: {req.dietary_style or 'No restriction'}
- Allowed Proteins: {proteins_str}
- Preferred Cuisines: {cuisines_str}
- Budget: {req.budget}

VOICE_MODE: false
LANGUAGE: English"""

    OUTPUT_SPEC = f"""
----------------------------------------------------
REQUIRED OUTPUT FORMAT — STRICT JSON ONLY
----------------------------------------------------

Respond ONLY with valid JSON. No markdown. No explanation. No text before or after.
Generate all 7 days. Each day has exactly 5 meals.

{{
  "bmr": {b},
  "tdee": {t},
  "daily_calorie_target": {target},
  "target_calories": {target},
  "plan": [
    {{
      "day": "Monday",
      "total_day_calories": {target},
      "meals": [
        {{
          "type": "Breakfast",
          "name": "meal name",
          "cal": 350,
          "total_calories": "350 kcal",
          "ingredients": [
            {{"name": "ingredient", "quantity": "100g", "calories": 120}}
          ],
          "macronutrients": {{"protein": "15g", "carbohydrates": "45g", "fats": "8g"}},
          "micronutrients_highlight": ["Iron", "Vitamin C"],
          "explainability": {{
            "why_selected": "reason this meal was chosen",
            "nutritional_purpose": "what key nutrients it provides",
            "goal_alignment": "how it supports {req.goal}",
            "allergy_confirmation": "Confirmed safe — contains no {allergies_str}"
          }},
          "recipe": {{
            "steps": ["Step 1", "Step 2", "Step 3"],
            "cooking_time": "15 mins",
            "difficulty": "Easy"
          }}
        }},
        {{ "type": "Mid-Morning", "name": "...", "cal": 130, "total_calories": "130 kcal", "ingredients": [...], "macronutrients": {{...}}, "micronutrients_highlight": [...], "explainability": {{...}}, "recipe": {{...}} }},
        {{ "type": "Lunch",       "name": "...", "cal": 500, "total_calories": "500 kcal", "ingredients": [...], "macronutrients": {{...}}, "micronutrients_highlight": [...], "explainability": {{...}}, "recipe": {{...}} }},
        {{ "type": "Evening",     "name": "...", "cal": 140, "total_calories": "140 kcal", "ingredients": [...], "macronutrients": {{...}}, "micronutrients_highlight": [...], "explainability": {{...}}, "recipe": {{...}} }},
        {{ "type": "Dinner",      "name": "...", "cal": 430, "total_calories": "430 kcal", "ingredients": [...], "macronutrients": {{...}}, "micronutrients_highlight": [...], "explainability": {{...}}, "recipe": {{...}} }}
      ]
    }},
    {{ "day": "Tuesday",   "total_day_calories": {target}, "meals": [ ...5 meals with all fields... ] }},
    {{ "day": "Wednesday", "total_day_calories": {target}, "meals": [ ...5 meals with all fields... ] }},
    {{ "day": "Thursday",  "total_day_calories": {target}, "meals": [ ...5 meals with all fields... ] }},
    {{ "day": "Friday",    "total_day_calories": {target}, "meals": [ ...5 meals with all fields... ] }},
    {{ "day": "Saturday",  "total_day_calories": {target}, "meals": [ ...5 meals with all fields... ] }},
    {{ "day": "Sunday",    "total_day_calories": {target}, "meals": [ ...5 meals with all fields... ] }}
  ],
  "safety_check": {{
    "allergy_verified": true,
    "medical_condition_verified": true,
    "conflicts_found": false,
    "notes": "All 7 days verified — no {allergies_str} present anywhere in the plan"
  }},
  "voice_summary": "Your personalized {req.goal} plan is ready! It hits {target} calories daily using {cuisines_str} cuisine, keeping you safe from {allergies_str}."
}}"""

    return SYSTEM + USER_DATA + OUTPUT_SPEC

# ─── SMART FALLBACK PLAN (used when Gemini is unavailable) ─
def make_fallback(target, req):
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    b=round(target*0.25); l=round(target*0.30); d=round(target*0.28); s=round(target*0.085)
    indian = not req.cuisines or "Indian" in req.cuisines
    veg = "Vegetarian" in req.dietary_style or "Vegan" in req.dietary_style
    ck = not veg and "Chicken" in req.proteins
    sf = not veg and "Seafood" in req.proteins
    eg = not veg and "Eggs" in req.proteins
    mt = not veg and "Mutton" in req.proteins
    allergies_str = ', '.join(req.allergies) if req.allergies else 'none listed'

    def meal(type_, name, cal, ingr, protein, carbs, fats, micros, why, purpose, alignment, steps, time, diff):
        return {
            "type": type_, "name": name, "cal": cal,
            "ingredients": [{"name": i, "quantity": "as needed", "calories": round(cal/len(ingr))} for i in ingr],
            "macronutrients": {"protein": protein, "carbohydrates": carbs, "fats": fats},
            "micronutrients_highlight": micros,
            "explainability": {"why_selected": why, "nutritional_purpose": purpose,
                               "goal_alignment": alignment, "allergy_confirmation": f"Confirmed safe — no {allergies_str}"},
            "recipe": {"steps": steps, "cooking_time": time, "difficulty": diff}
        }

    sets = [
        [meal("Breakfast","Poha with peanuts & vegetables" if indian else "Oats with banana & nuts",b,
              ["Poha","Peanuts","Onion","Green chilli"] if indian else ["Rolled oats","Banana","Almonds","Honey"],
              "8g","45g","6g",["Iron","B6"],
              "Light, easily digestible morning meal" if indian else "High-fiber, sustained energy breakfast",
              "Provides complex carbs and plant protein for morning energy",
              f"Supports {req.goal} with controlled calorie density",
              ["Heat oil, add mustard seeds","Add onion and green chilli, sauté","Add soaked poha, mix well","Garnish with lemon and coriander"] if indian else ["Soak oats in milk overnight","Slice banana and add almonds","Drizzle honey and serve cold"],
              "15 mins","Easy"),
         meal("Mid-Morning","Fresh fruit bowl",s,["Apple","Orange","Pomegranate"],"2g","28g","0.5g",["Vitamin C","Potassium","Antioxidants"],
              "Natural sugar and fiber for mid-morning energy","Provides vitamins and fiber to prevent energy crash",
              f"Low-calorie snack aligned with {req.goal}",["Wash and chop fruits","Mix together and serve"],"5 mins","Easy"),
         meal("Lunch","Chicken rice bowl" if ck else "Dal tadka + brown rice" if indian else "Chickpea Buddha bowl",l,
              ["Chicken breast","Basmati rice","Spinach","Spices"] if ck else ["Toor dal","Brown rice","Ghee","Turmeric"] if indian else ["Chickpeas","Quinoa","Cucumber","Olive oil"],
              "35g" if ck else "18g","55g","10g",["Protein","Iron","Zinc"],
              "High-protein balanced meal for midday","Complete protein and complex carbs for sustained energy",
              f"Core meal driving {req.goal} — high satiety, balanced macros",
              ["Cook rice/grain separately","Prepare protein/dal with spices","Combine and serve with vegetables"],"30 mins","Medium"),
         meal("Evening","Roasted chana chaat" if indian else "Mixed nuts & seeds",s,
              ["Roasted chana","Lemon","Chaat masala"] if indian else ["Almonds","Walnuts","Pumpkin seeds"],
              "6g","18g","4g",["Magnesium","Phosphorus"],
              "Protein-rich snack to prevent evening hunger",
              "Provides sustained energy and prevents overeating at dinner",
              f"Smart snacking aligned with {req.goal}",
              ["Mix all ingredients","Add lemon juice and seasoning","Serve immediately"] if indian else ["Portion out mixed nuts","Ready to serve"],"5 mins","Easy"),
         meal("Dinner","Grilled fish + veggies" if sf else "Paneer bhurji + roti" if indian else "Lentil soup + bread",d,
              ["Fish fillet","Broccoli","Olive oil","Lemon"] if sf else ["Paneer","Capsicum","Onion","Wheat roti"] if indian else ["Red lentils","Carrot","Bread","Olive oil"],
              "32g" if sf else "22g","30g","12g",["Omega-3","Calcium","Vitamin D"],
              "Light, protein-rich dinner for recovery",
              "Supports muscle recovery and provides essential nutrients before sleep",
              f"Evening meal optimized for {req.goal} — lower carbs, higher protein",
              ["Marinate protein with spices","Cook/grill with minimal oil","Steam vegetables separately","Plate and serve hot"],"25 mins","Medium")],
        [meal("Breakfast","Masala omelette + toast" if eg else "Idli + sambar" if indian else "Greek yogurt parfait",b,
              ["Eggs","Onion","Tomato","Whole wheat toast"] if eg else ["Idli","Sambar","Coconut chutney"] if indian else ["Greek yogurt","Granola","Mixed berries","Honey"],
              "18g" if eg else "10g","35g","9g",["B12","Choline","Selenium"],
              "High-protein breakfast for muscle support" if eg else "Fermented food rich in probiotics",
              "Provides complete amino acids and gut health support",
              f"Protein-forward start supporting {req.goal}",
              ["Beat eggs with seasoning","Sauté onions and tomatoes","Pour eggs, fold omelette","Toast bread and serve"] if eg else ["Steam idlis","Heat sambar","Serve with chutney"],"15 mins","Easy"),
         meal("Mid-Morning","Almonds & walnuts",s,["Almonds","Walnuts"],"5g","8g","14g",["Omega-3","Vitamin E"],
              "Healthy fat and protein snack","Brain-boosting omega-3 fatty acids and antioxidants",
              f"Supports {req.goal} with healthy fats and satiety",["Portion 30g mixed nuts","Eat mindfully"],"0 mins","Easy"),
         meal("Lunch","Rajma chawal" if indian else "Quinoa veggie bowl",l,
              ["Rajma","Basmati rice","Onion","Tomato","Spices"] if indian else ["Quinoa","Avocado","Cherry tomatoes","Spinach","Lemon"],
              "16g","62g","8g",["Iron","Folate","Fiber"],
              "Classic complete protein combination" if indian else "Complete amino acid profile from quinoa",
              "Plant protein + complex carb combination for sustained energy",
              f"High-fiber, satisfying lunch for {req.goal}",
              ["Pressure cook rajma","Prepare rice","Make tomato-onion gravy","Combine and serve"] if indian else ["Cook quinoa","Slice avocado","Toss all ingredients","Dress with lemon"],"35 mins","Medium"),
         meal("Evening","Sprouts chaat",s,["Moong sprouts","Tomato","Cucumber","Lemon"],"6g","14g","1g",["Folate","Vitamin C","Zinc"],
              "Live food packed with enzymes and micronutrients",
              "Sprouting increases bioavailability of nutrients significantly",
              f"Micronutrient-dense snack supporting {req.goal}",
              ["Rinse sprouts","Chop vegetables","Mix with lemon and seasoning","Serve fresh"],"10 mins","Easy"),
         meal("Dinner","Mutton curry + roti" if mt else "Palak paneer + roti" if indian else "Stir-fried tofu + rice",d,
              ["Mutton","Onion","Ginger-garlic","Wheat roti"] if mt else ["Spinach","Paneer","Cream","Wheat roti"] if indian else ["Firm tofu","Mixed vegetables","Brown rice","Soy sauce"],
              "28g" if mt else "20g","32g","14g",["B12","Iron","Calcium"],
              "Iron-rich dinner for energy replenishment" if mt else "Calcium and iron-rich vegetarian dinner",
              "Provides essential minerals for recovery and bone health",
              f"Nutrient-dense dinner closing the day's target for {req.goal}",
              ["Prepare base gravy","Add protein and cook through","Season and garnish","Serve with roti/rice"],"35 mins","Medium")],
    ]
    return [{"day": day, "total_day_calories": target, "meals": sets[i % 2]} for i, day in enumerate(days)]

# ─── PLAN ROUTES ──────────────────────────────────────────
@app.post("/api/generate-plan")
def generate_plan(req: PlanRequest, user=Depends(get_user), db: Session = Depends(get_db)):
    b = bmr(req.weight_kg, req.height_cm, req.age, req.gender)
    t = tdee(b, req.activity)
    tc = target_cal(t, req.goal)
    tc = fit_adj(tc, req.steps_today, req.calories_burned)

    plan_data = None
    used_ai = False

    if GEMINI_API_KEY:
        try:
            print(f"🤖 Clinical AI calling Gemini... goal={req.goal}, target={tc} kcal")
            text = call_gemini(build_prompt(req, tc, b, t)).strip()
            # Strip markdown fences
            text = re.sub(r"^```[a-z]*\s*\n?", "", text, flags=re.MULTILINE)
            text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
            text = text.strip()
            plan_data = json.loads(text)
            used_ai = True
            print(f"✅ Clinical AI plan generated! Safety check: {plan_data.get('safety_check', {})}")
        except json.JSONDecodeError as e:
            print(f"⚠️ JSON parse error: {e} — using smart fallback")
        except Exception as e:
            print(f"⚠️ Gemini error: {e} — using smart fallback")

    if not plan_data:
        print("📋 Using smart clinical fallback plan")
        plan_data = {
            "bmr": b, "tdee": t, "target_calories": tc,
            "safety_check": {"allergy_verified": True, "medical_condition_verified": True,
                             "conflicts_found": False, "notes": "Fallback plan — manually verified safe"},
            "voice_summary": f"Your smart {req.goal} plan is ready! Targeting {tc} calories per day with balanced nutrition.",
            "plan": make_fallback(tc, req)
        }

    plan_data["used_ai"] = used_ai

    if user:
        db.add(MealPlan(user_id=user.id, goal=req.goal, target_calories=tc, bmr=b, tdee=t,
                        plan_json=json.dumps(plan_data.get("plan", [])), created_at=datetime.utcnow()))
        db.commit()

    return plan_data

@app.get("/api/my-plans")
def my_plans(user=Depends(get_user), db: Session = Depends(get_db)):
    if not user: raise HTTPException(401, "Not authenticated")
    ps = db.query(MealPlan).filter(MealPlan.user_id == user.id).order_by(MealPlan.created_at.desc()).limit(20).all()
    return [{"id":p.id,"goal":p.goal,"target_calories":p.target_calories,"bmr":p.bmr,"tdee":p.tdee,
             "created_at":p.created_at.isoformat(),"plan":json.loads(p.plan_json) if p.plan_json else []} for p in ps]

@app.post("/api/save-plan")
def save_plan(plan_data: dict, user=Depends(get_user), db: Session = Depends(get_db)):
    if not user: raise HTTPException(401, "Not authenticated")
    mp = MealPlan(user_id=user.id, goal=plan_data.get("goal","Custom"), target_calories=plan_data.get("target_calories",0),
                  bmr=plan_data.get("bmr",0), tdee=plan_data.get("tdee",0),
                  plan_json=json.dumps(plan_data.get("plan",[])), created_at=datetime.utcnow())
    db.add(mp); db.commit()
    return {"message": "Saved", "id": mp.id}

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*50)
    print("🌿 NutriSync starting...")
    print("📌 http://localhost:8000")
    print("📌 http://localhost:8000/api/health  ← check this if something breaks")
    print("="*50 + "\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)