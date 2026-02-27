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

def build_prompt(req, target, b, t):
    return f"""You are NutriSync AI. Generate a 7-day meal plan.
USER: Age {req.age}, {req.gender}, {req.height_cm}cm, {req.weight_kg}kg
TARGETS: BMR={b} kcal, TDEE={t} kcal, Daily={target} kcal
GOAL: {req.goal} | ACTIVITY: {req.activity}
MEDICAL: {', '.join(req.medical_conditions) or 'None'}
DIET: {req.dietary_style or 'No restriction'} | PROTEINS: {', '.join(req.proteins) or 'All'}
ALLERGIES: {', '.join(req.allergies) or 'None'}
CUISINES: {', '.join(req.cuisines) or 'Any'} | BUDGET: {req.budget}

OUTPUT ONLY VALID JSON. No markdown. No explanation. Start with {{ and end with }}.
{{"bmr":{b},"tdee":{t},"target_calories":{target},"plan":[{{"day":"Monday","meals":[{{"type":"Breakfast","name":"meal name","cal":320,"ingredients":["item1","item2"]}},{{"type":"Mid-Morning","name":"snack","cal":100,"ingredients":["item1"]}},{{"type":"Lunch","name":"meal name","cal":500,"ingredients":["item1","item2"]}},{{"type":"Evening","name":"snack","cal":120,"ingredients":["item1"]}},{{"type":"Dinner","name":"meal name","cal":450,"ingredients":["item1","item2"]}}]}},{{"day":"Tuesday",...}},{{"day":"Wednesday",...}},{{"day":"Thursday",...}},{{"day":"Friday",...}},{{"day":"Saturday",...}},{{"day":"Sunday",...}}]}}
Total daily cals ≈ {target}. Respect all restrictions. Use {', '.join(req.cuisines) or 'varied'} cuisine."""

def make_fallback(target, req):
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    b=round(target*0.25); l=round(target*0.30); d=round(target*0.28); s=round(target*0.085)
    indian = not req.cuisines or "Indian" in req.cuisines
    veg = "Vegetarian" in req.dietary_style or "Vegan" in req.dietary_style
    ck = not veg and "Chicken" in req.proteins
    sf = not veg and "Seafood" in req.proteins
    eg = not veg and "Eggs" in req.proteins
    mt = not veg and "Mutton" in req.proteins

    sets = [
        [{"type":"Breakfast","name":"Poha with peanuts & veggies" if indian else "Oats with banana & nuts","cal":b,"ingredients":["Poha","Peanuts","Onion"] if indian else ["Oats","Banana","Almond"]},
         {"type":"Mid-Morning","name":"Fruit bowl","cal":s,"ingredients":["Apple","Orange","Pomegranate"]},
         {"type":"Lunch","name":"Chicken rice bowl" if ck else "Dal tadka + brown rice","cal":l,"ingredients":["Chicken","Rice","Spices"] if ck else ["Toor dal","Brown rice","Ghee"]},
         {"type":"Evening","name":"Roasted chana chaat","cal":s,"ingredients":["Roasted chana","Lemon","Chaat masala"]},
         {"type":"Dinner","name":"Grilled fish + vegetables" if sf else "Paneer bhurji + 2 rotis" if indian else "Lentil soup","cal":d,"ingredients":["Fish","Broccoli","Olive oil"] if sf else ["Paneer","Capsicum","Wheat flour"] if indian else ["Lentils","Bread","Olive oil"]}],
        [{"type":"Breakfast","name":"Masala omelette + toast" if eg else "Idli + sambar" if indian else "Greek yogurt bowl","cal":b,"ingredients":["Eggs","Onion","Tomato"] if eg else ["Idli","Sambar","Coconut chutney"] if indian else ["Greek yogurt","Granola","Honey"]},
         {"type":"Mid-Morning","name":"Mixed nuts","cal":s,"ingredients":["Almonds","Walnuts","Cashews"]},
         {"type":"Lunch","name":"Rajma chawal" if indian else "Chickpea Buddha bowl","cal":l,"ingredients":["Rajma","Rice","Onion"] if indian else ["Chickpeas","Quinoa","Veggies"]},
         {"type":"Evening","name":"Sprouts chaat","cal":s,"ingredients":["Sprouts","Tomato","Lemon"]},
         {"type":"Dinner","name":"Mutton curry + roti" if mt else "Palak paneer + roti" if indian else "Stir fried tofu + rice","cal":d,"ingredients":["Mutton","Spices","Onion"] if mt else ["Spinach","Paneer","Cream"] if indian else ["Tofu","Veggies","Soy sauce"]}],
        [{"type":"Breakfast","name":"Upma with vegetables" if indian else "Smoothie bowl","cal":b,"ingredients":["Semolina","Mustard","Curry leaves"] if indian else ["Banana","Berries","Granola"]},
         {"type":"Mid-Morning","name":"Papaya with lime","cal":s,"ingredients":["Papaya","Lime"]},
         {"type":"Lunch","name":"Chole + 2 rotis" if indian else "Quinoa salad","cal":l,"ingredients":["Chickpeas","Onion","Spices"] if indian else ["Quinoa","Avocado","Lemon"]},
         {"type":"Evening","name":"Makhana (fox nuts)","cal":s,"ingredients":["Makhana","Ghee","Salt"]},
         {"type":"Dinner","name":"Egg curry + rice" if eg else "Mixed veg curry + rice" if indian else "Pasta primavera","cal":d,"ingredients":["Eggs","Tomato","Spices"] if eg else ["Mixed veg","Coconut milk","Spices"] if indian else ["Pasta","Zucchini","Olive oil"]}],
    ]
    return [{"day": day, "meals": sets[i % 3]} for i, day in enumerate(days)]

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
            print(f"🤖 Calling Gemini... goal={req.goal}, target={tc} kcal")
            text = call_gemini(build_prompt(req, tc, b, t)).strip()
            text = re.sub(r"^```[a-z]*\s*\n?", "", text); text = re.sub(r"\n?```\s*$", "", text)
            text = text.strip()
            plan_data = json.loads(text)
            used_ai = True
            print("✅ Gemini success!")
        except Exception as e:
            print(f"⚠️ Gemini error: {e}")

    if not plan_data:
        print("📋 Using smart fallback plan")
        plan_data = {"bmr": b, "tdee": t, "target_calories": tc, "plan": make_fallback(tc, req)}

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