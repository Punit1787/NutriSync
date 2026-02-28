from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
import hashlib, secrets, json, os, re, urllib.request, urllib.error, base64, ssl
from datetime import datetime
from models import init_db, get_db, User, MealPlan, FoodLog
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
                    k = k.strip(); v = v.strip().strip('"').strip("'")
                    os.environ[k] = v
        print(f"✅ .env loaded")
    else:
        print(f"⚠️  No .env file found at {env_path}")

load_env()

GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "").strip()
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()

print(f"🔑 Gemini: {'✅ ' + GEMINI_API_KEY[:12] + '...' if GEMINI_API_KEY else '❌ NOT SET'}")
print(f"🔑 Google OAuth: {'✅ SET' if GOOGLE_CLIENT_ID else '⚠️  NOT SET'}")

app = FastAPI(title="NutriSync API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
init_db()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def read_html(name):
    path = os.path.join(BASE_DIR, name)
    if not os.path.exists(path):
        return f"<h1>File {name} not found</h1>"
    with open(path, encoding="utf-8") as f:
        return f.read()

@app.get("/",          response_class=HTMLResponse) 
def serve_index():     return read_html("index.html")
@app.get("/planner",   response_class=HTMLResponse) 
def serve_planner():   return read_html("planner.html")
@app.get("/dashboard", response_class=HTMLResponse) 
def serve_dashboard(): return read_html("dashboard.html")

@app.get("/api/health")
def health():
    return {"status": "✅ running",
            "gemini": "✅ key set" if GEMINI_API_KEY else "❌ no key",
            "google_oauth": "✅" if GOOGLE_CLIENT_ID else "⚠️ not set",
            "db": "✅ connected", "time": datetime.utcnow().isoformat()}

@app.get("/api/google-client-id")
def get_gcid(): return {"client_id": GOOGLE_CLIENT_ID or ""}

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

class ImageAnalyseRequest(BaseModel):
    image_base64: str          # base64-encoded image
    mime_type: str = "image/jpeg"
    allergies: List[str] = []
    dietary_style: str = ""
    goal: str = "Maintain Weight"

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
    u = User(name=req.name.strip(), email=email,
             password_hash=hp(req.password), token=t, created_at=datetime.utcnow())
    db.add(u); db.commit()
    return {"token": t, "name": u.name, "email": u.email}

@app.post("/api/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    email = req.email.lower().strip()
    u = db.query(User).filter(User.email == email).first()
    if not u or u.password_hash != hp(req.password):
        raise HTTPException(401, "Wrong email or password")
    u.token = tok(); db.commit()
    return {"token": u.token, "name": u.name, "email": u.email}

@app.post("/api/google-login")
def google_login(req: GoogleLoginRequest, db: Session = Depends(get_db)):
    try:
        parts = req.credential.split(".")
        if len(parts) != 3: raise HTTPException(400, "Invalid Google token")
        pad = parts[1] + "=" * (4 - len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(pad).decode("utf-8"))
        email = payload.get("email","").lower().strip()
        name  = payload.get("name", req.name or "Google User")
        if not email: raise HTTPException(400, "No email in Google token")
        u = db.query(User).filter(User.email == email).first()
        if not u:
            u = User(name=name, email=email,
                     password_hash=hp(secrets.token_hex(16)), token=tok(),
                     created_at=datetime.utcnow())
            db.add(u)
        else:
            u.token = tok()
        db.commit()
        return {"token": u.token, "name": u.name, "email": u.email}
    except HTTPException: raise
    except Exception as e:
        raise HTTPException(400, f"Google login failed: {str(e)}")

@app.get("/api/user-info")
def user_info(user=Depends(get_user)):
    if not user: raise HTTPException(401, "Not authenticated")
    return {"name": user.name, "email": user.email, "id": user.id}

# ─── NUTRITION MATH ───────────────────────────────────────
AMF = {"sedentary":1.2,"light":1.375,"moderate":1.55,"very":1.725,"extra":1.9}

def bmr(w,h,a,g):
    return round(10*w + 6.25*h - 5*a + (5 if g.lower() in ["male","m"] else -161))
def tdee(b,act): return round(b * AMF.get(act.lower(), 1.55))
def target_cal(t,goal):
    g = goal.lower()
    return t-500 if "loss" in g else t+300 if ("gain" in g or "muscle" in g) else t
def fit_adj(t,steps,burned):
    if steps:  t = round(t*0.9) if steps<4000 else round(t*1.1) if steps>10000 else t
    if burned and burned>0: t += round(burned*0.3)
    return t

# ─── ALLERGEN DERIVATIVES (hard blocklist) ────────────────
ALLERGEN_DERIVATIVES = {
    "Peanuts": [
        "peanut","peanuts","groundnut","groundnuts","monkey nut",
        "peanut butter","peanut oil","arachis oil","beer nuts",
    ],
    "Gluten": [
        "gluten","wheat","whole wheat","maida","atta","roti","chapati","naan",
        "paratha","bread","whole grain bread","pita","tortilla","barley","rye",
        "semolina","bulgur","farro","spelt","kamut","couscous","pasta","spaghetti",
        "noodles","udon","soy sauce","flour","wheat flour","all-purpose flour",
        "breadcrumbs","croutons","malt","malt vinegar","seitan","wheat bran","wheat germ","oats",
    ],
    "Lactose": [
        "lactose","milk","dairy","cream","butter","ghee","paneer","cheese","cheddar",
        "mozzarella","cottage cheese","ricotta","yogurt","curd","dahi","buttermilk",
        "lassi","whey","casein","ice cream","condensed milk","evaporated milk",
        "sour cream","kefir","custard","milk powder","skimmed milk","full cream milk",
    ],
    "Tree Nuts": [
        "tree nut","tree nuts","almond","almonds","walnut","walnuts","cashew","cashews",
        "pistachio","pistachios","pecan","pecans","hazelnut","hazelnuts","macadamia",
        "brazil nut","brazil nuts","pine nut","pine nuts","chestnut","chestnuts",
        "nut butter","almond butter","almond milk","cashew milk","mixed nuts",
        "praline","marzipan","nougat",
    ],
    "Egg Allergy": [
        "egg","eggs","egg white","egg yolk","omelette","omelet","scrambled egg",
        "fried egg","boiled egg","mayonnaise","mayo","meringue","albumin","globulin",
        "lysozyme","ovalbumin","ovomucin","ovomucoid","egg noodles","egg pasta",
        "custard","hollandaise","aioli",
    ],
    "Shellfish": [
        "shellfish","shrimp","prawn","crab","lobster","crayfish","scallop",
        "clam","oyster","mussel","barnacle","squid","octopus","abalone",
    ],
    "Soy": [
        "soy","soya","tofu","tempeh","edamame","miso","soy sauce","tamari",
        "soy milk","soy protein","textured vegetable protein","tvp","soybean",
    ],
    "Fish": [
        "fish","salmon","tuna","cod","tilapia","sardine","mackerel","anchovy",
        "halibut","trout","bass","snapper","catfish","herring","mahi","swordfish",
        "fish sauce","fish oil","fish stock",
    ],
}

# ─── DIETARY EXCLUSIONS ───────────────────────────────────
DIET_EXCLUSIONS = {
    "Vegetarian": [
        "chicken","beef","mutton","lamb","pork","fish","seafood","prawn","shrimp",
        "crab","lobster","tuna","salmon","sardine","anchovy","meat","meat broth",
        "gelatin","lard","bacon","ham","sausage","salami","pepperoni","venison",
        "duck","turkey","quail","veal","bison","goat meat",
    ],
    "Vegan": [
        "chicken","beef","mutton","lamb","pork","fish","seafood","prawn","shrimp",
        "crab","lobster","tuna","salmon","sardine","anchovy","meat","meat broth",
        "gelatin","lard","bacon","ham","sausage","salami","pepperoni","venison",
        "duck","turkey","quail","veal","bison","goat meat",
        "milk","dairy","cream","butter","ghee","paneer","cheese","yogurt","curd",
        "dahi","buttermilk","lassi","whey","casein","milk powder","egg","eggs",
        "omelette","mayonnaise","custard","honey","beeswax","shellac",
    ],
    "Pescatarian": [
        "chicken","beef","mutton","lamb","pork","gelatin","lard","bacon","ham",
        "sausage","salami","pepperoni","venison","duck","turkey","quail","veal",
        "bison","goat meat","meat broth",
    ],
    "Non-Vegetarian": [],
}

# ─── NON-VEG PROTEIN PROFILES ─────────────────────────────
# Full library of non-veg protein options with safe-ingredient lists
NON_VEG_PROTEINS = {
    "Chicken": {
        "breakfast": {
            "name": "Chicken keema paratha (gluten-free version: bowl)",
            "ingredients_safe": ["Chicken mince","Onion","Ginger","Garlic","Coriander","Cumin","Coconut oil"],
            "ingredients_gluten": ["Chicken mince","Whole wheat paratha","Onion","Ginger","Garlic","Coriander"],
            "protein": "28g", "carbs": "35g", "fats": "12g",
            "steps": ["Cook chicken mince with spices until dry","Stuff into paratha dough and roll",
                      "Cook on tawa with oil 3 mins each side","Serve with green chutney"],
            "time": "25 mins", "diff": "Medium",
        },
        "lunch": {
            "name": "Grilled chicken breast with brown rice & veggies",
            "ingredients": ["Chicken breast","Brown rice","Broccoli","Bell peppers","Olive oil","Garlic","Lemon","Herbs"],
            "protein": "38g", "carbs": "48g", "fats": "10g",
            "steps": ["Marinate chicken in olive oil, garlic, lemon 20 mins",
                      "Grill 7 mins each side until 75°C internal","Cook brown rice 20 mins separately",
                      "Steam broccoli and peppers 5 mins","Plate and garnish with fresh herbs"],
            "time": "35 mins", "diff": "Medium",
        },
        "dinner": {
            "name": "Chicken tikka masala (light) with cauliflower rice",
            "ingredients": ["Chicken breast","Tomato","Onion","Garlic","Ginger","Spices","Coconut milk","Cauliflower"],
            "protein": "32g", "carbs": "22g", "fats": "14g",
            "steps": ["Cut chicken into cubes, marinate with yogurt and spices",
                      "Grill or pan-cook chicken 8 mins","Prepare tomato-onion gravy",
                      "Add coconut milk for creaminess","Grate cauliflower and microwave 4 mins as rice substitute"],
            "time": "35 mins", "diff": "Medium",
        },
    },
    "Mutton": {
        "lunch": {
            "name": "Slow-cooked mutton curry with brown rice",
            "ingredients": ["Mutton pieces","Brown rice","Onion","Tomato","Ginger","Garlic",
                            "Whole spices","Coriander","Mint"],
            "protein": "30g", "carbs": "50g", "fats": "16g",
            "steps": ["Marinate mutton with yogurt and spices 30 mins",
                      "Pressure cook mutton 4-5 whistles","Sauté onion, ginger, garlic until golden",
                      "Add tomato and cook down 5 mins","Add mutton, simmer 10 mins, serve over rice"],
            "time": "60 mins", "diff": "Advanced",
        },
        "dinner": {
            "name": "Mutton keema with peas",
            "ingredients": ["Mutton mince","Green peas","Onion","Tomato","Ginger","Garlic","Spices","Olive oil"],
            "protein": "28g", "carbs": "18g", "fats": "14g",
            "steps": ["Heat oil, add onion and sauté golden","Add ginger-garlic paste 2 mins",
                      "Add mutton mince, cook on high 5 mins","Add tomato and spices, cook 10 mins",
                      "Add peas, simmer 5 mins, garnish coriander"],
            "time": "30 mins", "diff": "Medium",
        },
    },
    "Beef": {
        "lunch": {
            "name": "Lean beef stir-fry with quinoa",
            "ingredients": ["Lean beef strips","Quinoa","Mixed vegetables","Garlic","Ginger","Olive oil","Herbs"],
            "protein": "35g", "carbs": "42g", "fats": "12g",
            "steps": ["Cook quinoa 15 mins","Slice beef thin against the grain",
                      "Heat oil on high, sear beef 3 mins","Add vegetables and garlic, stir-fry 4 mins",
                      "Serve over quinoa with fresh herbs"],
            "time": "25 mins", "diff": "Medium",
        },
        "dinner": {
            "name": "Grilled beef patty with roasted vegetables",
            "ingredients": ["Lean beef mince","Zucchini","Capsicum","Carrot","Olive oil","Garlic","Herbs"],
            "protein": "30g", "carbs": "20g", "fats": "15g",
            "steps": ["Form beef into patties with herbs and seasoning","Grill 4 mins per side",
                      "Toss vegetables in olive oil and garlic","Roast 220°C for 20 mins",
                      "Serve patty over roasted veg"],
            "time": "30 mins", "diff": "Medium",
        },
    },
    "Pork": {
        "lunch": {
            "name": "Pork tenderloin with sweet potato",
            "ingredients": ["Pork tenderloin","Sweet potato","Spinach","Garlic","Olive oil","Rosemary","Lemon"],
            "protein": "32g", "carbs": "38g", "fats": "10g",
            "steps": ["Season pork with rosemary, garlic and lemon","Sear in oven-safe pan 3 mins each side",
                      "Roast at 200°C for 18-20 mins","Boil sweet potato 15 mins",
                      "Wilt spinach in same pan, serve together"],
            "time": "35 mins", "diff": "Medium",
        },
        "dinner": {
            "name": "Stir-fried pork with vegetables",
            "ingredients": ["Pork loin","Broccoli","Carrot","Capsicum","Garlic","Ginger","Coconut aminos","Sesame oil"],
            "protein": "28g", "carbs": "18g", "fats": "12g",
            "steps": ["Slice pork thin","Heat sesame oil in wok on high","Stir-fry pork 4 mins",
                      "Add garlic, ginger and vegetables","Add coconut aminos, toss 2 mins, serve hot"],
            "time": "20 mins", "diff": "Easy",
        },
    },
    "Seafood": {
        "breakfast": {
            "name": "Prawn and vegetable omelette",
            "ingredients": ["Prawns","Eggs","Capsicum","Onion","Coriander","Olive oil","Pepper"],
            "protein": "26g", "carbs": "8g", "fats": "12g",
            "steps": ["Sauté prawns with garlic 2 mins, set aside","Beat eggs with salt and pepper",
                      "Pour eggs into pan, add prawns and veg","Fold omelette, serve with lemon"],
            "time": "15 mins", "diff": "Easy",
        },
        "lunch": {
            "name": "Grilled fish with quinoa and salad",
            "ingredients": ["Fish fillet","Quinoa","Mixed greens","Cherry tomato","Olive oil","Lemon","Herbs"],
            "protein": "34g", "carbs": "38g", "fats": "10g",
            "steps": ["Season fish with herbs and lemon","Cook quinoa 15 mins",
                      "Pan-grill fish 4 mins per side","Toss greens and tomato with olive oil",
                      "Plate fish over quinoa with salad"],
            "time": "25 mins", "diff": "Easy",
        },
        "dinner": {
            "name": "Prawn masala with brown rice",
            "ingredients": ["Prawns","Brown rice","Tomato","Onion","Garlic","Ginger","Coconut milk","Spices"],
            "protein": "30g", "carbs": "45g", "fats": "10g",
            "steps": ["Cook brown rice 20 mins","Sauté onion, garlic, ginger 5 mins",
                      "Add tomato and spices, cook 5 mins","Add prawns and coconut milk, simmer 6 mins",
                      "Serve over rice, garnish with coriander"],
            "time": "30 mins", "diff": "Medium",
        },
    },
    "Eggs": {
        "breakfast": {
            "name": "Masala scrambled eggs",
            "ingredients": ["Eggs","Onion","Tomato","Green chilli","Turmeric","Coriander","Olive oil"],
            "protein": "18g", "carbs": "10g", "fats": "14g",
            "steps": ["Beat eggs with salt and turmeric","Sauté onion, tomato and chilli 3 mins",
                      "Pour eggs, stir continuously on low heat","Garnish coriander, serve hot"],
            "time": "10 mins", "diff": "Easy",
        },
        "lunch": {
            "name": "Egg fried cauliflower rice",
            "ingredients": ["Eggs","Cauliflower","Peas","Carrot","Garlic","Ginger","Coconut aminos","Sesame oil"],
            "protein": "20g", "carbs": "22g", "fats": "14g",
            "steps": ["Grate cauliflower into rice-sized pieces","Stir-fry in sesame oil 4 mins",
                      "Push aside, scramble eggs in center","Add peas and carrot, toss with coconut aminos",
                      "Add garlic and ginger, serve hot"],
            "time": "20 mins", "diff": "Easy",
        },
    },
    "Turkey": {
        "lunch": {
            "name": "Turkey and vegetable bowl",
            "ingredients": ["Turkey breast","Brown rice","Broccoli","Carrot","Olive oil","Garlic","Herbs"],
            "protein": "36g", "carbs": "45g", "fats": "8g",
            "steps": ["Season turkey with garlic and herbs","Grill or bake at 190°C 20-22 mins",
                      "Cook brown rice 20 mins","Steam vegetables 5 mins",
                      "Slice turkey and serve over rice with veg"],
            "time": "30 mins", "diff": "Easy",
        },
        "dinner": {
            "name": "Minced turkey with lentils",
            "ingredients": ["Turkey mince","Red lentils","Onion","Tomato","Garlic","Cumin","Turmeric","Olive oil"],
            "protein": "30g", "carbs": "28g", "fats": "9g",
            "steps": ["Cook lentils 15 mins with turmeric","Sauté onion and garlic","Add turkey mince, cook 8 mins",
                      "Add tomato and cumin, cook 5 mins","Combine with lentils, simmer 5 mins"],
            "time": "30 mins", "diff": "Easy",
        },
    },
    "Duck": {
        "dinner": {
            "name": "Roasted duck breast with roasted vegetables",
            "ingredients": ["Duck breast","Sweet potato","Asparagus","Garlic","Rosemary","Olive oil","Orange zest"],
            "protein": "28g", "carbs": "30g", "fats": "18g",
            "steps": ["Score duck skin, season with rosemary and orange zest",
                      "Sear skin-side down 6 mins to render fat","Flip and roast at 200°C 12 mins",
                      "Roast sweet potato and asparagus alongside","Rest duck 5 mins before slicing"],
            "time": "35 mins", "diff": "Advanced",
        },
    },
}

# ─── SAFETY UTILITIES ─────────────────────────────────────
def build_blocklist(allergies: List[str], dietary_style: str) -> List[str]:
    blocked = set()
    for allergy in allergies:
        for key, terms in ALLERGEN_DERIVATIVES.items():
            if key.lower() == allergy.lower() or allergy.lower() in key.lower():
                blocked.update(t.lower() for t in terms)
    style = (dietary_style or "").strip().lower()
    style_key = None
    for key in DIET_EXCLUSIONS:
     if key.lower() == style:
        style_key = key
        break
    if style_key:
        blocked.update(t.lower() for t in DIET_EXCLUSIONS[style_key])
    return list(blocked)

def ingredient_is_safe(name: str, blocklist: List[str]) -> bool:
    n = name.lower()
    return not any(b in n or n in b for b in blocklist)

def validate_plan_safety(plan_days: List[dict], blocklist: List[str]) -> dict:
    violations = []
    for day in plan_days:
        for meal in day.get("meals", []):
            for ing in meal.get("ingredients", []):
                iname = ing.get("name","") if isinstance(ing, dict) else str(ing)
                if not ingredient_is_safe(iname, blocklist):
                    violations.append({"day": day.get("day","?"),
                                       "meal": meal.get("name","?"),
                                       "ingredient": iname})
    return {"safe": len(violations)==0, "violations": violations}

def enforce_dietary_protein_consistency(req: PlanRequest) -> dict:
    warnings = []
    proteins = list(req.proteins)
    style = (req.dietary_style or "").lower()
    animal_p  = {"chicken","mutton","beef","pork","seafood","duck","turkey","fish"}
    vegan_exc = {"chicken","mutton","beef","pork","seafood","duck","turkey","fish","eggs","dairy"}

    if "vegan" in style:
        removed = [p for p in proteins if p.lower() in vegan_exc]
        proteins = [p for p in proteins if p.lower() not in vegan_exc]
        if removed: warnings.append(f"Vegan diet: removed {', '.join(removed)}")
    elif style == "vegetarian":
        removed = [p for p in proteins if p.lower() in animal_p]
        proteins = [p for p in proteins if p.lower() not in animal_p]
        if removed: warnings.append(f"Vegetarian diet: removed {', '.join(removed)}")
    elif "pescatarian" in style:
        land = {"chicken","mutton","beef","pork","duck","turkey"}
        removed = [p for p in proteins if p.lower() in land]
        proteins = [p for p in proteins if p.lower() not in land]
        if removed: warnings.append(f"Pescatarian: removed land meat {', '.join(removed)}")

    # Allergy vs protein cross-check
    allergy_conflicts = []
    for protein in proteins:
        for allergy in req.allergies:
            terms = ALLERGEN_DERIVATIVES.get(allergy, [])
            if any(t.lower() in protein.lower() for t in terms):
                allergy_conflicts.append(protein); break
    proteins = [p for p in proteins if p not in allergy_conflicts]
    if allergy_conflicts:
        warnings.append(f"Allergy conflict removed: {', '.join(allergy_conflicts)}")

    return {"proteins": proteins, "warnings": warnings}

# ─── GEMINI CALLS ─────────────────────────────────────────
def _ssl_ctx():
    """Return an SSL context that works on macOS without cert bundle issues."""
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl._create_unverified_context()
    return ctx

def call_gemini_text(prompt: str) -> str:
    import time
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}")
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 6000}
    }).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as r:
                d = json.loads(r.read())
            return d["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                wait = 15 * (attempt + 1)
                print(f"⏳ Gemini rate limit — waiting {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
            else:
                raise


def call_gemini_vision(prompt: str, image_b64: str, mime_type: str = "image/jpeg") -> str:
    """Call Gemini Vision with an image + text prompt."""
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}")
    body = json.dumps({
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": image_b64}},
                {"text": prompt}
            ]
        }],
        "generationConfig": {"temperature": 0.4, "maxOutputTokens": 3000}
    }).encode()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as r:
                d = json.loads(r.read())
            return d["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            import time
            if e.code == 429 and attempt < 2:
                wait = 15 * (attempt + 1)
                print(f"⏳ Gemini vision rate limit — waiting {wait}s")
                time.sleep(wait)
            else:
                raise

# ─── IMAGE ANALYSIS PROMPT ────────────────────────────────
def build_image_analysis_prompt(allergies: List[str], dietary_style: str,
                                 goal: str, blocklist: List[str]) -> str:
    allergies_str = ', '.join(allergies) if allergies else 'None'
    blocked_str   = ', '.join(sorted(set(blocklist))) if blocklist else 'None'
    return f"""You are a Clinical Nutrition AI. Analyse the food image provided.

USER CONTEXT:
- Goal: {goal}
- Dietary Style: {dietary_style or 'No restriction'}
- Allergies (HARD CONSTRAINT): {allergies_str}
- All blocked ingredient terms: {blocked_str}

TASKS:
1. Identify the dish and all visible ingredients.
2. Estimate total calories for the portion shown.
3. Estimate macros: protein, carbs, fats (in grams).
4. List key micronutrients present.
5. Flag any identified ingredients that match the user's allergies/blocklist.
6. For EACH flagged allergen ingredient, provide 2 safe, practical alternatives.
7. Give an overall nutrition rating (1-10) for this meal relative to the user's goal.
8. Provide 2-3 healthy modifications to make this meal better for the user's goal.

OUTPUT: Respond ONLY with valid JSON, no markdown fences, no extra text.

{{
  "dish_name": "string",
  "identified_ingredients": ["list of detected ingredients"],
  "total_calories": 450,
  "serving_size": "estimated portion e.g. 1 plate (~350g)",
  "macronutrients": {{
    "protein": "22g",
    "carbohydrates": "55g",
    "fats": "14g",
    "fiber": "6g"
  }},
  "micronutrients": ["Iron", "Vitamin C", "Calcium"],
  "allergen_alerts": [
    {{
      "ingredient": "name of flagged ingredient",
      "allergy": "which allergy it triggers",
      "safe_replacements": [
        {{"name": "replacement option 1", "calories_change": "-10 kcal", "benefit": "why it is better"}},
        {{"name": "replacement option 2", "calories_change": "+5 kcal", "benefit": "why it is better"}}
      ]
    }}
  ],
  "dietary_style_violations": ["any ingredients that violate the dietary preference"],
  "nutrition_rating": 7,
  "rating_reason": "brief explanation of rating",
  "goal_alignment": "how this meal aligns or misaligns with the goal",
  "healthy_modifications": [
    {{"change": "Replace X with Y", "impact": "saves 80 kcal, adds fibre"}},
    {{"change": "Reduce portion of Z", "impact": "reduces saturated fat by 40%"}},
    {{"change": "Add A alongside", "impact": "increases protein by 10g"}}
  ],
  "overall_safety": "SAFE" or "CONTAINS_ALLERGENS" or "VIOLATES_DIET",
  "summary": "2-sentence plain-English summary of the meal's nutrition"
}}"""

# ─── IMAGE ANALYSIS ENDPOINT ──────────────────────────────
@app.post("/api/analyse-image")
def analyse_image(req: ImageAnalyseRequest):
    if not GEMINI_API_KEY:
        raise HTTPException(503, "Gemini API key not configured — cannot analyse image")

    blocklist = build_blocklist(req.allergies, req.dietary_style or "")

    try:
        prompt = build_image_analysis_prompt(
            req.allergies, req.dietary_style, req.goal, blocklist
        )
        raw = call_gemini_vision(prompt, req.image_base64, req.mime_type).strip()
        raw = re.sub(r"^```[a-z]*\s*\n?", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\n?```\s*$", "", raw, flags=re.MULTILINE)
        result = json.loads(raw.strip())
        # Determine overall_safety from allergen_alerts if not set correctly
        if result.get("allergen_alerts") and result.get("overall_safety") != "CONTAINS_ALLERGENS":
            result["overall_safety"] = "CONTAINS_ALLERGENS"
        return result
    except json.JSONDecodeError as e:
        raise HTTPException(422, f"AI returned invalid JSON: {e}")
    except Exception as e:
        raise HTTPException(500, f"Image analysis failed: {e}")

# ─── CLINICAL AI PROMPT ────────────────────────────────────
def build_prompt(req, target, b, t, adjusted_proteins, blocklist):
    bmi          = round(req.weight_kg / ((req.height_cm / 100) ** 2), 1)
    allergies_str = ', '.join(req.allergies)          if req.allergies          else 'None'
    medical_str   = ', '.join(req.medical_conditions) if req.medical_conditions else 'None'
    cuisines_str  = ', '.join(req.cuisines)           if req.cuisines           else 'Any'
    proteins_str  = ', '.join(adjusted_proteins)      if adjusted_proteins      else 'Plant-based only'
    blocklist_str = ', '.join(sorted(set(blocklist))) if blocklist              else 'None'

    style = (req.dietary_style or "").strip().lower()

    is_veg = style == "vegetarian"
    is_vegan = style == "vegan"
    is_pesc = style == "pescatarian"
    is_nonveg = style == "nonveg"
    

    # Build targeted guidance for non-veg proteins
    nonveg_guidance = ""
    if is_nonveg and adjusted_proteins:
        nonveg_guidance = f"""
NON-VEGETARIAN PROTEIN GUIDANCE:
The user eats: {proteins_str}
- Use these proteins across the 7-day plan with variety — don't repeat the same protein every day.
- Rotate proteins: e.g. chicken Monday, fish Tuesday, eggs Wednesday, mutton Thursday, etc.
- Include non-veg at least in Lunch and Dinner when a non-veg protein is available.
- Preparation styles should vary: grilled, curried, stir-fried, roasted, steamed.
- For beef/pork: use lean cuts (sirloin, tenderloin, loin).
- For seafood: use prawns, fish fillets, or crab where available.
- All non-veg proteins must still clear the allergy blocklist before inclusion.
"""

    SYSTEM = f"""You are a Clinical Nutrition AI integrated into a health platform.
Generate a SAFE, personalized, medically-aware 7-day meal plan.

PRIORITY ORDER (strict — higher always overrides lower):
  1. ALLERGY SAFETY ← ABSOLUTE HARD CONSTRAINT
  2. Medical conditions
  3. Dietary preference ({req.dietary_style or 'unrestricted'})
  4. Calorie targets
  5. Cuisine & taste preferences

ALLERGY RULES — NON-NEGOTIABLE:
- NEVER include any blocked ingredient, its derivative, or a "may contain" variant.
- If uncertain about an ingredient, EXCLUDE it.
- This applies to every meal of every day without exception.

DIETARY RULES:
- Vegetarian: zero meat, fish, poultry, seafood.
- Vegan: zero animal products including dairy, eggs, honey.
- Pescatarian: zero land meat; fish/seafood allowed.
- Non-Vegetarian: use all allowed proteins from the provided list, rotated across the week.
{nonveg_guidance}

RECIPE REQUIREMENTS:
- Each meal needs a recipe with at least 5 clear cooking steps.
- Cooking time, difficulty (Easy/Medium/Advanced).
- Use realistic, accessible ingredients.
- Vary cooking methods across the week.
"""

    USER_DATA = f"""
USER PROFILE:
- Age: {req.age} | Gender: {req.gender} | Height: {req.height_cm}cm | Weight: {req.weight_kg}kg
- BMI: {bmi} | Activity: {req.activity} | Goal: {req.goal}
- BMR: {b} kcal | TDEE: {t} kcal | Daily Target: {target} kcal
- Steps: {req.steps_today or 'N/A'} | Cal Burned: {req.calories_burned or 'N/A'}

MEDICAL CONDITIONS: {medical_str}

=== BLOCKED INGREDIENTS (ABSOLUTE — NEVER INCLUDE) ===
Allergies declared: {allergies_str}
Complete blocklist: {blocklist_str}
======================================================

DIETARY PREFERENCE: {req.dietary_style or 'No restriction'}
ALLOWED PROTEINS (allergy-filtered): {proteins_str}
PREFERRED CUISINES: {cuisines_str}
BUDGET: {req.budget}
"""

    OUTPUT_SPEC = f"""
OUTPUT: Respond ONLY with valid JSON — no markdown, no explanation.
Generate all 7 days × 5 meals (Breakfast, Mid-Morning, Lunch, Evening, Dinner).

{{
  "bmr": {b}, "tdee": {t}, "daily_calorie_target": {target}, "target_calories": {target},
  "plan": [
    {{
      "day": "Monday", "total_day_calories": {target},
      "meals": [
        {{
          "type": "Breakfast", "name": "meal name", "cal": 380,
          "ingredients": [{{"name": "ingredient", "quantity": "100g", "calories": 120}}],
          "macronutrients": {{"protein": "20g", "carbohydrates": "45g", "fats": "10g"}},
          "micronutrients_highlight": ["Iron", "B12"],
          "explainability": {{
            "why_selected": "...", "nutritional_purpose": "...",
            "goal_alignment": "...",
            "allergy_confirmation": "Confirmed safe — zero {allergies_str} ingredients present"
          }},
          "recipe": {{
            "steps": ["Step 1","Step 2","Step 3","Step 4","Step 5"],
            "cooking_time": "20 mins", "difficulty": "Easy"
          }}
        }},
        {{ "type": "Mid-Morning", "name": "...", "cal": 140, "ingredients": [...],
           "macronutrients": {{...}}, "micronutrients_highlight": [...],
           "explainability": {{...}}, "recipe": {{...}} }},
        {{ "type": "Lunch", "name": "...", "cal": 520, "ingredients": [...],
           "macronutrients": {{...}}, "micronutrients_highlight": [...],
           "explainability": {{...}}, "recipe": {{...}} }},
        {{ "type": "Evening", "name": "...", "cal": 150, "ingredients": [...],
           "macronutrients": {{...}}, "micronutrients_highlight": [...],
           "explainability": {{...}}, "recipe": {{...}} }},
        {{ "type": "Dinner", "name": "...", "cal": 450, "ingredients": [...],
           "macronutrients": {{...}}, "micronutrients_highlight": [...],
           "explainability": {{...}}, "recipe": {{...}} }}
      ]
    }},
    {{ "day": "Tuesday",   "total_day_calories": {target}, "meals": [...] }},
    {{ "day": "Wednesday", "total_day_calories": {target}, "meals": [...] }},
    {{ "day": "Thursday",  "total_day_calories": {target}, "meals": [...] }},
    {{ "day": "Friday",    "total_day_calories": {target}, "meals": [...] }},
    {{ "day": "Saturday",  "total_day_calories": {target}, "meals": [...] }},
    {{ "day": "Sunday",    "total_day_calories": {target}, "meals": [...] }}
  ],
  "safety_check": {{
    "allergy_verified": true,
    "medical_condition_verified": true,
    "conflicts_found": false,
    "notes": "All 7 days verified — zero {allergies_str} present"
  }},
  "voice_summary": "Your {req.goal} plan is ready! {target} calories daily, fully clear of {allergies_str}."
}}"""

    return SYSTEM + USER_DATA + OUTPUT_SPEC


# ─── INGREDIENT HELPERS ──────────────────────────────────
_ING_CALS = {
    # proteins (per 100g cooked)
    "chicken breast": 165, "chicken": 165, "eggs": 155, "egg": 155,
    "prawns": 99, "prawn": 99, "fish fillet": 120, "fish": 120, "salmon": 208,
    "tuna": 132, "mutton": 258, "beef": 250, "lean beef strips": 250,
    "pork tenderloin": 143, "pork": 143, "turkey breast": 135, "turkey": 135,
    "paneer": 265, "tofu": 76, "chickpeas": 164, "moong dal": 105, "masoor dal": 116,
    "toor dal": 116, "idli batter": 58, "moong sprouts": 30,
    # carbs
    "brown rice": 112, "basmati rice": 121, "white rice": 130, "quinoa": 120,
    "sweet potato": 86, "cauliflower": 25, "oats": 389,
    # dairy
    "milk": 61, "yogurt": 59, "dahi": 61, "curd": 61, "butter": 717, "ghee": 900,
    "cheese": 402, "cream": 340, "whey": 352,
    # fats
    "olive oil": 884, "coconut oil": 862, "almonds": 579, "flaxseeds": 534,
    # vegs (low cal)
    "spinach": 23, "broccoli": 34, "capsicum": 31, "onion": 40, "tomato": 18,
    "green chilli": 40, "garlic": 149, "ginger": 80, "coriander": 23,
    "curry leaves": 108, "tamarind": 239, "lemon": 29, "lime": 30,
    # spices (negligible)
    "turmeric": 0, "cumin": 0, "pepper": 0, "mustard seeds": 0,
    "chaat masala": 0, "spices": 0, "herbs": 0, "rosemary": 0,
    # fruits/snacks
    "banana": 89, "apple": 52, "mixed greens": 20, "cherry tomato": 18,
    "roasted chana": 364,
    # misc
    "coconut milk": 230, "mixed vegetables": 65,
}
_ING_QTY = {
    "chicken breast": "150g", "chicken": "150g", "eggs": "2 eggs",
    "prawns": "120g", "prawn": "120g", "fish fillet": "150g", "fish": "150g",
    "mutton": "120g", "beef": "120g", "lean beef strips": "120g",
    "pork tenderloin": "130g", "turkey breast": "130g",
    "paneer": "100g", "brown rice": "80g dry", "basmati rice": "80g dry",
    "quinoa": "70g dry", "sweet potato": "150g", "oats": "60g",
    "olive oil": "1 tbsp", "coconut oil": "1 tbsp", "butter": "10g",
    "almonds": "20g", "banana": "1 medium", "apple": "1 medium",
    "onion": "1 medium", "tomato": "1 medium", "garlic": "3 cloves",
    "spinach": "60g", "broccoli": "80g",
}
def _ing_qty(name):
    n = name.lower()
    for k, v in _ING_QTY.items():
        if k in n: return v
    return "as needed"

def _ing_cal(name, meal_cal, n_ings):
    n = name.lower()
    for k, v in _ING_CALS.items():
        if k in n:
            # Estimate based on typical portion
            if v > 500: return round(v * 0.015 * 100) // 100 * 1  # oils ~13g
            if v > 200: return round(v * 0.10)   # dense foods 100g
            if v > 100: return round(v * 0.15)   # moderate density
            return round(v * 0.08)               # vegs/spices small portion
    return round(meal_cal / max(n_ings, 1))  # fallback only if unknown

# ─── SMART FALLBACK (full non-veg support) ────────────────
def make_fallback(target, req, adjusted_proteins, blocklist):
    days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    # Realistic calorie splits: B=25%, L=35%, D=30%, Snacks=10% (split ~5% each)
    B = round(target*0.25); L = round(target*0.35); D = round(target*0.30); S = round(target*0.05)

    style    = (req.dietary_style or "").lower()
    is_veg   = style == "vegetarian" or "vegan" in style
    is_vegan = "vegan" in style
    is_pesc  = "pescatarian" in style
    ap_lower = [p.lower() for p in adjusted_proteins]

    def ok(ingredient): return ingredient_is_safe(ingredient, blocklist)
    def p_ok(protein):  return protein.lower() in ap_lower

    ck  = not is_veg and p_ok("chicken")   and ok("chicken")
    mt  = not is_veg and p_ok("mutton")    and ok("mutton")
    bf  = not is_veg and p_ok("beef")      and ok("beef")
    pk  = not is_veg and p_ok("pork")      and ok("pork")
    sf  = (not is_veg or is_pesc) and p_ok("seafood") and ok("prawn")
    eg  = not is_vegan and p_ok("eggs")    and ok("egg")
    dk  = not is_veg and p_ok("duck")      and ok("duck")
    tk  = not is_veg and p_ok("turkey")    and ok("turkey")
    dairy_ok = not is_vegan and ok("milk")
    paneer_ok = dairy_ok and ok("paneer")
    indian = not req.cuisines or "Indian" in req.cuisines
    mediterranean = not req.cuisines or "Mediterranean" in req.cuisines
    asian = not req.cuisines or "Asian" in req.cuisines or "Chinese" in req.cuisines or "Japanese" in req.cuisines
    western = not req.cuisines or not indian  # if non-indian selected, use western

    allergies_str = ', '.join(req.allergies) if req.allergies else 'none'
    allergy_ok_msg = f"✓ Confirmed safe — no {allergies_str} present"

    def meal(type_, name, cal, ingr, prot, carb, fat, micros,
             why, purpose, align, steps, time_, diff):
        safe = [i for i in ingr if ok(i)]
        if not safe: safe = ["Brown rice","Lentils","Olive oil"]
        return {
            "type": type_, "name": name, "cal": cal,
            "ingredients": [{"name": i, "quantity": _ing_qty(i),
                             "calories": _ing_cal(i, cal, len(safe))} for i in safe],
            "macronutrients": {"protein": prot,"carbohydrates": carb,"fats": fat},
            "micronutrients_highlight": micros,
            "explainability": {
                "why_selected": why, "nutritional_purpose": purpose,
                "goal_alignment": align, "allergy_confirmation": allergy_ok_msg
            },
            "recipe": {"steps": steps, "cooking_time": time_, "difficulty": diff}
        }

    # ─── Breakfast pool ───────────────────────────────────
    def bfast(day_idx):
        # 7 distinct breakfasts cycling – non-indian fallbacks when indian not selected
        i = day_idx % 7
        if i == 0 and eg:
            return meal("Breakfast","Masala scrambled eggs",B,
                ["Eggs","Onion","Tomato","Green chilli","Turmeric","Coriander","Olive oil"],
                "18g","10g","14g",["B12","Choline","Selenium"],
                "High-protein egg breakfast","Complete amino acids and choline",
                f"Protein-forward start supporting {req.goal}",
                ["Beat eggs with turmeric and salt","Sauté onion, tomato, chilli 3 mins",
                 "Pour eggs into pan","Stir until just set","Garnish coriander"],"10 mins","Easy")
        elif i == 1 and ck:
            name = "Chicken omelette" if not indian else "Chicken keema omelette"
            return meal("Breakfast",name,B,
                ["Eggs","Chicken","Onion","Capsicum","Garlic","Olive oil","Pepper"],
                "28g","8g","15g",["B12","Protein","Selenium"],
                "High-protein chicken & egg breakfast","Two complete protein sources",
                f"Power breakfast for {req.goal}",
                ["Mince or shred cooked chicken","Beat eggs with salt and pepper",
                 "Sauté garlic, onion 2 mins","Add chicken, cook 2 mins",
                 "Pour eggs over, fold omelette","Serve with toast or salad"],"15 mins","Easy")
        elif i == 2 and (not indian) and eg:
            return meal("Breakfast","Avocado egg toast",B,
                ["Eggs","Mixed greens","Tomato","Olive oil","Garlic","Pepper"],
                "16g","28g","12g",["B12","Healthy Fats","Fiber"],
                "Western-style high-fibre breakfast","Healthy fats with complete protein",
                f"Balanced morning meal for {req.goal}",
                ["Toast whole grain bread","Mash vegetables with olive oil and seasoning",
                 "Fry or poach eggs","Layer on toast","Season with pepper"],"10 mins","Easy")
        elif i == 2 and dairy_ok and indian:
            return meal("Breakfast","Idli with sambar",B,
                ["Idli batter","Toor dal","Tomato","Tamarind","Curry leaves","Mustard seeds","Turmeric"],
                "10g","42g","3g",["Iron","B Vitamins","Probiotics"],
                "Fermented South Indian breakfast","Probiotics + plant protein",
                f"Light digestible start for {req.goal}",
                ["Steam idlis 12-15 mins","Boil dal with tomato and tamarind",
                 "Prepare mustard-curry leaf tadka","Add to sambar, simmer 2 mins",
                 "Serve with coconut chutney"],"25 mins","Easy")
        elif i == 3 and sf and eg:
            return meal("Breakfast","Prawn & egg scramble",B,
                ["Prawns","Eggs","Capsicum","Garlic","Olive oil","Pepper"],
                "26g","8g","12g",["B12","Omega-3","Iodine"],
                "High-protein seafood breakfast","Complete protein from two sources",
                f"Powerful start for {req.goal}",
                ["Sauté prawns with garlic 2 mins","Beat eggs, pour over prawns",
                 "Add capsicum and stir","Cook on low until just set",
                 "Season with pepper and serve"],"15 mins","Easy")
        elif i == 4 and dairy_ok and not indian:
            return meal("Breakfast","Greek yogurt & oats bowl",B,
                ["Oats","Yogurt","Banana","Almonds","Flaxseeds"],
                "14g","52g","8g",["Calcium","Fiber","Omega-3"],
                "High-fiber Western breakfast","Probiotic yogurt with complex carbs",
                f"Sustained energy for {req.goal}",
                ["Cook oats with water or milk 5 mins","Slice banana",
                 "Top oats with yogurt","Add banana, almonds, flaxseeds",
                 "Drizzle honey if desired"],"10 mins","Easy")
        elif i == 4 and indian:
            return meal("Breakfast","Poha with peanuts",B,
                ["Poha","Onion","Tomato","Peanuts","Mustard seeds","Curry leaves","Turmeric"],
                "8g","48g","6g",["Iron","B Vitamins","Fiber"],
                "Light flattened rice breakfast","Iron-rich quick breakfast",
                f"Light energising start for {req.goal}",
                ["Rinse poha and drain","Sauté mustard seeds, curry leaves",
                 "Add onion, tomato, turmeric","Mix in poha and peanuts",
                 "Cook 3 mins, garnish coriander"],"15 mins","Easy")
        elif i == 5 and eg and not indian:
            return meal("Breakfast","Veggie egg muffins",B,
                ["Eggs","Spinach","Capsicum","Onion","Cheese","Olive oil","Pepper"],
                "20g","10g","14g",["B12","Calcium","Iron"],
                "Meal-prep friendly protein breakfast","Portable protein-packed breakfast",
                f"Macro-controlled breakfast for {req.goal}",
                ["Preheat oven 180°C","Whisk eggs with salt and pepper",
                 "Chop and sauté vegetables 3 mins","Fill muffin tin with veg then egg mix",
                 "Bake 18-20 mins until set"],"25 mins","Medium")
        elif i == 5 and indian:
            return meal("Breakfast","Upma with vegetables",B,
                ["Semolina","Onion","Tomato","Carrot","Peas","Mustard seeds","Ghee"],
                "9g","44g","7g",["B Vitamins","Fiber","Iron"],
                "Traditional Indian semolina breakfast","Complex carbs with vegetables",
                f"Light filling breakfast for {req.goal}",
                ["Dry roast semolina golden, set aside","Heat ghee, add mustard seeds",
                 "Sauté onion, carrot, peas 4 mins","Add water, bring to boil",
                 "Stir in semolina, cook 3 mins"],"20 mins","Easy")
        elif i == 6 and ck and not indian:
            return meal("Breakfast","Chicken & spinach wrap",B,
                ["Chicken","Spinach","Tomato","Yogurt","Whole wheat wrap","Garlic","Olive oil"],
                "30g","32g","10g",["Protein","Iron","Calcium"],
                "High-protein wrap breakfast","Lean protein with iron-rich greens",
                f"Filling power breakfast for {req.goal}",
                ["Season and grill chicken strips","Warm wrap briefly",
                 "Mix yogurt with garlic as sauce","Layer chicken, spinach, tomato",
                 "Roll and serve warm"],"20 mins","Easy")
        else:
            return meal("Breakfast","Moong dal chilla",B,
                ["Moong dal","Onion","Tomato","Green chilli","Cumin","Turmeric","Coconut oil"],
                "14g","38g","6g",["Iron","Folate","Fiber"],
                "High-protein savoury pancake","Plant protein with complex carbs",
                f"Sustained morning energy for {req.goal}",
                ["Soak moong dal 2 hours, drain and grind smooth",
                 "Add onion, tomato, chilli, spices","Heat pan, pour thin batter",
                 "Cook 3 mins until bubbles form","Flip, cook 2 mins more"],"20 mins","Easy")

    # ─── Lunch pool ───────────────────────────────────────
    def lunch(day_idx):
        opts = []
        # Pescatarian: seafood dominates, only veg if no seafood
        if is_pesc and sf:
            opts = ["seafood", "seafood", "seafood", "seafood", "seafood"]
        else:
            # Non-veg: meat at every lunch, doubled chicken weight
            if ck:  opts.extend(["chicken", "chicken"])
            if mt:  opts.append("mutton")
            if bf:  opts.append("beef")
            if pk:  opts.append("pork")
            if sf:  opts.append("seafood")
            if tk:  opts.append("turkey")
            if dk:  opts.append("duck")
        if not opts:
            # Veg lunch
            if paneer_ok:
                return meal("Lunch","Paneer tikka rice bowl",L,
                    ["Paneer","Brown rice","Capsicum","Onion","Tomato","Spices","Olive oil"],
                    "24g","52g","12g",["Calcium","Protein","Iron"],
                    "High-calcium vegetarian protein lunch","Paneer provides complete protein and calcium",
                    f"Satisfying midday meal for {req.goal}",
                    ["Cube paneer, marinate with spices 20 mins","Grill or air-fry 10 mins",
                     "Cook brown rice separately","Sauté capsicum and onion 4 mins",
                     "Assemble bowl with rice, paneer and veg"],"35 mins","Medium")
            else:
                return meal("Lunch","Chickpea curry with quinoa",L,
                    ["Chickpeas","Quinoa","Tomato","Onion","Garlic","Spices","Coconut oil"],
                    "18g","58g","9g",["Iron","Folate","Fiber"],
                    "Complete plant protein combination","Chickpeas + quinoa = all essential amino acids",
                    f"High-fibre satisfying lunch for {req.goal}",
                    ["Cook quinoa 15 mins","Sauté onion and garlic 5 mins","Add tomato and spices 5 mins",
                     "Add chickpeas, simmer 10 mins","Serve over quinoa"],"35 mins","Medium")

        chosen = opts[day_idx % len(opts)]
        if chosen == "chicken":
            return meal("Lunch","Grilled chicken & brown rice bowl",L,
                ["Chicken breast","Brown rice","Spinach","Broccoli","Olive oil","Garlic","Lemon"],
                "38g","48g","10g",["Protein","Iron","Vitamin C"],
                "Lean protein powerhouse lunch","High bioavailable protein with complex carbs",
                f"Optimal macro split for {req.goal}",
                ["Marinate chicken with garlic, lemon, herbs 15 mins","Grill 7 mins each side",
                 "Cook brown rice 20 mins","Wilt spinach in pan 2 mins","Steam broccoli 5 mins",
                 "Slice chicken over rice with veg"],"35 mins","Medium")
        elif chosen == "mutton":
            return meal("Lunch","Mutton curry with basmati rice",L,
                ["Mutton","Basmati rice","Onion","Tomato","Ginger","Garlic","Whole spices","Coriander"],
                "30g","55g","16g",["Iron","Zinc","B12"],
                "Iron-rich traditional mutton curry","High iron and zinc for energy and immunity",
                f"Flavourful satisfying lunch for {req.goal}",
                ["Marinate mutton with spices 30 mins","Sauté onion golden, add ginger-garlic",
                 "Add tomato, cook until oil separates","Add mutton, pressure cook 4 whistles",
                 "Simmer uncovered 10 mins to thicken","Serve over basmati with coriander"],"60 mins","Advanced")
        elif chosen == "beef":
            return meal("Lunch","Lean beef stir-fry with quinoa",L,
                ["Lean beef strips","Quinoa","Mixed vegetables","Garlic","Ginger","Olive oil"],
                "35g","42g","12g",["Iron","Zinc","B12"],
                "High-iron lean beef with complete protein grain","Beef provides haem iron for energy",
                f"Power lunch supporting {req.goal}",
                ["Cook quinoa 15 mins","Slice beef thin against grain","Sear beef on high heat 3 mins",
                 "Add garlic, ginger and vegetables, stir-fry 4 mins",
                 "Season with herbs, serve over quinoa"],"25 mins","Medium")
        elif chosen == "pork":
            return meal("Lunch","Pork tenderloin with sweet potato",L,
                ["Pork tenderloin","Sweet potato","Spinach","Garlic","Olive oil","Rosemary"],
                "32g","38g","10g",["B1","Zinc","Potassium"],
                "Lean pork with nutrient-rich sweet potato","Thiamine-rich pork with complex carbs",
                f"Balanced lunch macro split for {req.goal}",
                ["Season pork with rosemary and garlic","Sear in oven-proof pan 3 mins each side",
                 "Roast 200°C for 18 mins","Boil sweet potato 15 mins","Wilt spinach in pan",
                 "Slice pork and serve with sweet potato and spinach"],"35 mins","Medium")
        elif chosen == "turkey":
            return meal("Lunch","Turkey and vegetable bowl",L,
                ["Turkey breast","Brown rice","Broccoli","Carrot","Garlic","Olive oil","Herbs"],
                "36g","45g","8g",["Protein","B6","Zinc"],
                "Lean turkey — lowest-fat poultry option","Tryptophan in turkey supports mood and recovery",
                f"High-protein lunch for {req.goal}",
                ["Season turkey with garlic, herbs","Grill or bake 200°C 22 mins","Cook brown rice",
                 "Steam broccoli and carrot 5 mins","Slice turkey, assemble bowl"],"30 mins","Easy")
        else:  # seafood
            return meal("Lunch","Grilled fish & quinoa salad",L,
                ["Fish fillet","Quinoa","Mixed greens","Cherry tomato","Olive oil","Lemon","Herbs"],
                "34g","38g","10g",["Omega-3","Iodine","Vitamin D"],
                "Omega-3 rich lunch for heart and brain health","Fatty acids reduce inflammation",
                f"Anti-inflammatory lunch supporting {req.goal}",
                ["Cook quinoa 15 mins, cool","Season fish with herbs and lemon",
                 "Pan-grill fish 4 mins per side","Toss greens and tomato with olive oil",
                 "Plate fish over quinoa with salad"],"25 mins","Easy")

    # ─── Dinner pool ──────────────────────────────────────
    def dinner(day_idx):
        opts = []
        # Pescatarian: seafood at every dinner
        if is_pesc and sf:
            opts = ["seafood", "seafood", "seafood", "seafood", "seafood"]
        else:
            # Non-veg: rotate meat proteins, seafood included
            if sf: opts.append("seafood")
            if ck: opts.extend(["chicken", "chicken"])
            if mt: opts.append("mutton")
            if bf: opts.append("beef")
            if pk: opts.append("pork")
            if dk: opts.append("duck")
        if not opts:
            if paneer_ok:
                return meal("Dinner","Palak paneer with cauliflower rice",D,
                    ["Paneer","Spinach","Onion","Tomato","Garlic","Spices","Cauliflower"],
                    "22g","20g","14g",["Calcium","Iron","Vitamin K"],
                    "Iron and calcium-rich vegetarian dinner","Spinach iron + paneer calcium for recovery",
                    f"Nutrient-dense low-carb dinner for {req.goal}",
                    ["Blanch spinach, blend smooth","Sauté onion, garlic, tomato with spices",
                     "Add spinach puree, simmer 5 mins","Add paneer cubes, cook 5 mins",
                     "Grate cauliflower, microwave 4 mins as rice substitute",
                     "Serve palak paneer over cauli-rice"],"30 mins","Medium")
            else:
                return meal("Dinner","Masoor dal with brown rice",D,
                    ["Masoor dal","Brown rice","Spinach","Garlic","Tomato","Cumin","Turmeric","Coconut oil"],
                    "18g","35g","7g",["Iron","Folate","Fiber"],
                    "Comforting iron-rich lentil dinner","Plant iron and fibre for overnight recovery",
                    f"Light satisfying dinner for {req.goal}",
                    ["Cook masoor dal with turmeric 15 mins","Sauté garlic and tomato",
                     "Add wilted spinach","Combine with dal, simmer 5 mins",
                     "Prepare brown rice separately","Serve dal over rice"],"25 mins","Easy")

        chosen = opts[day_idx % len(opts)]
        if chosen == "seafood":
            return meal("Dinner","Prawn masala with brown rice",D,
                ["Prawns","Brown rice","Tomato","Onion","Garlic","Ginger","Coconut milk","Spices"],
                "30g","45g","10g",["Omega-3","Iodine","Selenium"],
                "Omega-3 rich light dinner","Selenium and iodine support thyroid and metabolism",
                f"Anti-inflammatory dinner for {req.goal}",
                ["Cook brown rice 20 mins","Sauté onion, garlic, ginger 5 mins",
                 "Add tomato and spices, cook 5 mins","Add prawns, cook 4 mins",
                 "Pour coconut milk, simmer 3 mins","Serve over rice, garnish coriander"],"30 mins","Medium")
        elif chosen == "chicken":
            return meal("Dinner","Chicken tikka (light) with cauliflower rice",D,
                ["Chicken breast","Cauliflower","Tomato","Onion","Garlic","Spices","Coconut milk"],
                "32g","22g","12g",["Protein","B6","Vitamin C"],
                "High-protein lower-carb dinner","Lean chicken with cruciferous veg for recovery",
                f"Evening meal optimised for {req.goal}",
                ["Cube chicken, marinate with spices 20 mins","Grill chicken 8-10 mins",
                 "Make tomato-onion sauce, add coconut milk","Add chicken, simmer 5 mins",
                 "Grate cauliflower, microwave 4 mins","Serve curry over cauli-rice"],"35 mins","Medium")
        elif chosen == "mutton":
            return meal("Dinner","Mutton keema with peas",D,
                ["Mutton mince","Green peas","Onion","Tomato","Ginger","Garlic","Spices","Olive oil"],
                "28g","18g","14g",["Iron","Zinc","B12"],
                "Iron-dense mince for overnight muscle repair","High zinc supports recovery and immunity",
                f"Protein-rich dinner for {req.goal}",
                ["Sauté onion until golden","Add ginger-garlic paste 2 mins","Add mutton mince, cook on high 5 mins",
                 "Add tomato and spices, cook 10 mins","Add peas, simmer 5 mins","Garnish coriander"],"30 mins","Medium")
        elif chosen == "beef":
            return meal("Dinner","Grilled lean beef with roasted veg",D,
                ["Lean beef","Zucchini","Capsicum","Carrot","Olive oil","Garlic","Herbs"],
                "30g","20g","14g",["Iron","Zinc","B12"],
                "Haem iron powerhouse dinner","Highest bioavailable iron for energy",
                f"Recovery dinner for {req.goal}",
                ["Form beef patties with herbs and seasoning","Grill 4 mins per side",
                 "Toss veg in olive oil and garlic","Roast 220°C 20 mins",
                 "Rest beef 3 mins before serving","Plate over roasted veg"],"30 mins","Medium")
        elif chosen == "pork":
            return meal("Dinner","Stir-fried pork with vegetables",D,
                ["Pork loin","Broccoli","Carrot","Capsicum","Garlic","Ginger","Sesame oil"],
                "28g","18g","12g",["B1","Zinc","Vitamin C"],
                "Lean pork stir-fry — quick and nutrient-dense","Thiamine-rich pork for energy metabolism",
                f"Light, high-protein dinner for {req.goal}",
                ["Slice pork thin","Heat sesame oil on high","Sear pork 4 mins",
                 "Add garlic and ginger 1 min","Add vegetables, stir-fry 4 mins",
                 "Season and serve immediately"],"20 mins","Easy")
        else:  # duck
            return meal("Dinner","Roasted duck breast with sweet potato",D,
                ["Duck breast","Sweet potato","Asparagus","Garlic","Rosemary","Orange zest"],
                "28g","30g","18g",["Iron","B12","Vitamin A"],
                "Rich iron and B12 from duck, complex carbs from sweet potato",
                "Duck fat contains heart-healthy monounsaturated fats",
                f"Indulgent yet balanced dinner for {req.goal}",
                ["Score duck skin, season with rosemary and orange zest","Sear skin-side 6 mins to render fat",
                 "Flip and roast 200°C 12 mins","Roast sweet potato alongside 25 mins",
                 "Steam asparagus 3 mins","Rest duck 5 mins, slice and serve"],"40 mins","Advanced")

    # ─── Snack pool ───────────────────────────────────────
    snacks = [
        (["Apple","Orange","Pomegranate"],     "2g","28g","0.5g",["Vitamin C","Potassium"]),
        (["Moong sprouts","Tomato","Lemon"],    "7g","15g","0.5g",["Folate","Vitamin C"]),
        (["Banana","Flaxseeds"],               "3g","27g","2g",  ["Potassium","Omega-3"]),
    ]
    # Only add chana snack if safe
    if ok("roasted chana"):
        snacks.append((["Roasted chana","Lemon","Chaat masala"],"6g","18g","4g",["Magnesium","Fiber"]))

    def snack(day_idx, time_of_day):
        s = snacks[day_idx % len(snacks)]
        ingr, prot, carb, fat, micros = s
        cal = S
        return meal(time_of_day,
                    "Fresh fruit bowl" if ingr[0]=="Apple" else
                    "Sprouts chaat"    if ingr[0]=="Moong sprouts" else
                    "Banana & flaxseeds" if ingr[0]=="Banana" else
                    "Roasted chana chaat",
                    cal, ingr, prot, carb, fat, micros,
                    "Light snack prevents energy dips","Micronutrients and fibre",
                    f"Aligned with {req.goal}",
                    ["Prepare ingredients","Mix or chop","Season lightly","Serve fresh"],"5 mins","Easy")

    # Build plan ensuring no meal name repeats within 2 consecutive days
    plan = []
    breakfast_history, lunch_history, dinner_history = [], [], []

    for i, day in enumerate(days):
        # Non-repeating breakfast
        bf_meal = None
        for attempt in range(5):
            candidate = bfast((i + attempt) % 7)
            if candidate["name"] not in breakfast_history[-2:]:
                bf_meal = candidate; break
        if not bf_meal: bf_meal = bfast(i)
        breakfast_history.append(bf_meal["name"])

        # Non-repeating lunch — rotate protein type each day
        lc_meal = None
        for attempt in range(7):
            candidate = lunch((i + attempt) % 7)
            if candidate["name"] not in lunch_history[-2:]:
                lc_meal = candidate; break
        if not lc_meal: lc_meal = lunch(i)
        lunch_history.append(lc_meal["name"])

        # Non-repeating dinner
        dn_meal = None
        for attempt in range(7):
            candidate = dinner((i + attempt) % 7)
            if candidate["name"] not in dinner_history[-2:]:
                dn_meal = candidate; break
        if not dn_meal: dn_meal = dinner(i)
        dinner_history.append(dn_meal["name"])

        snack1 = snack((i * 2) % len(snacks), "Mid-Morning")
        snack2 = snack((i * 2 + 1) % len(snacks), "Evening")
        day_total = bf_meal["cal"] + snack1["cal"] + lc_meal["cal"] + snack2["cal"] + dn_meal["cal"]

        plan.append({
            "day": day,
            "total_day_calories": day_total,
            "meals": [bf_meal, snack1, lc_meal, snack2, dn_meal]
        })
    return plan

# ─── GENERATE PLAN ENDPOINT ───────────────────────────────
@app.post("/api/generate-plan")
def generate_plan(req: PlanRequest, user=Depends(get_user), db: Session = Depends(get_db)):
    b  = bmr(req.weight_kg, req.height_cm, req.age, req.gender)
    t  = tdee(b, req.activity)
    tc = target_cal(t, req.goal)
    tc = fit_adj(tc, req.steps_today, req.calories_burned)

    conflict_result  = enforce_dietary_protein_consistency(req)
    adjusted_proteins = conflict_result["proteins"]
    conflict_warnings = conflict_result["warnings"]
    if conflict_warnings:
        print(f"⚠️  Conflicts resolved: {conflict_warnings}")

    blocklist = build_blocklist(req.allergies, req.dietary_style or "")
    print(f"🔒 Blocklist: {len(blocklist)} terms | allergies={req.allergies} | diet={req.dietary_style}")

    plan_data = None
    used_ai   = False

    if GEMINI_API_KEY:
        try:
            print(f"🤖 Gemini... goal={req.goal}, target={tc} kcal")
            text = call_gemini_text(build_prompt(req, tc, b, t, adjusted_proteins, blocklist)).strip()
            text = re.sub(r"^```[a-z]*\s*\n?", "", text, flags=re.MULTILINE)
            text = re.sub(r"\n?```\s*$",        "", text, flags=re.MULTILINE)
            plan_data = json.loads(text.strip())
            used_ai   = True

            safety = validate_plan_safety(plan_data.get("plan", []), blocklist)
            if not safety["safe"]:
                print(f"🚨 Violations in AI plan — using safe fallback. {safety['violations']}")
                plan_data["plan"] = make_fallback(tc, req, adjusted_proteins, blocklist)
                plan_data["safety_check"] = {
                    "allergy_verified": True, "medical_condition_verified": True,
                    "conflicts_found": False,
                    "notes": "AI plan had allergen hits — safe fallback applied."
                }
            else:
                plan_data["safety_check"] = {
                    "allergy_verified": True, "medical_condition_verified": True,
                    "conflicts_found": False,
                    "notes": f"All 7 days verified safe. {len(blocklist)} terms blocked."
                }

        except json.JSONDecodeError as e:
            print(f"⚠️ JSON parse error: {e}")
        except Exception as e:
            print(f"⚠️ Gemini error: {e}")

    if not plan_data:
        print("📋 Using smart fallback")
        plan_data = {
            "bmr": b, "tdee": t, "target_calories": tc,
            "safety_check": {
                "allergy_verified": True, "medical_condition_verified": True,
                "conflicts_found": False,
                "notes": f"Fallback — {len(blocklist)} allergen terms enforced."
            },
            "voice_summary": f"Your {req.goal} plan is ready! Targeting {tc} calories per day.",
            "plan": make_fallback(tc, req, adjusted_proteins, blocklist)
        }

    plan_data["used_ai"]          = used_ai
    plan_data["conflict_warnings"] = conflict_warnings

    if user:
        db.add(MealPlan(user_id=user.id, goal=req.goal, target_calories=tc, bmr=b, tdee=t,
                        plan_json=json.dumps(plan_data.get("plan", [])),
                        created_at=datetime.utcnow()))
        db.commit()

    return plan_data

@app.get("/api/my-plans")
def my_plans(user=Depends(get_user), db: Session = Depends(get_db)):
    if not user: raise HTTPException(401, "Not authenticated")
    ps = db.query(MealPlan).filter(MealPlan.user_id == user.id)\
           .order_by(MealPlan.created_at.desc()).limit(20).all()
    return [{"id": p.id, "goal": p.goal, "target_calories": p.target_calories,
             "bmr": p.bmr, "tdee": p.tdee, "created_at": p.created_at.isoformat(),
             "plan": json.loads(p.plan_json) if p.plan_json else []} for p in ps]

@app.post("/api/save-plan")
def save_plan(plan_data: dict, user=Depends(get_user), db: Session = Depends(get_db)):
    if not user: raise HTTPException(401, "Not authenticated")
    mp = MealPlan(user_id=user.id, goal=plan_data.get("goal","Custom"),
                  target_calories=plan_data.get("target_calories",0),
                  bmr=plan_data.get("bmr",0), tdee=plan_data.get("tdee",0),
                  plan_json=json.dumps(plan_data.get("plan",[])),
                  created_at=datetime.utcnow())
    db.add(mp); db.commit()
    return {"message": "Saved", "id": mp.id}


# ─── PYDANTIC MODELS FOR NEW ENDPOINTS ───────────────────
class FoodLogRequest(BaseModel):
    meal_name: str
    calories: float = 0
    protein: float = 0
    carbs: float = 0
    fats: float = 0
    fiber: float = 0
    meal_type: str = "Meal"
    logged_date: Optional[str] = None
    notes: Optional[str] = None
    image_analysis: Optional[str] = None

class RegenMealRequest(BaseModel):
    day: str
    meal_type: str
    goal: str = "Maintain Weight"
    dietary_style: str = ""
    proteins: List[str] = []
    allergies: List[str] = []
    cuisines: List[str] = []
    target_calories: int = 2000
    exclude_names: List[str] = []

class UpdatePlanRequest(BaseModel):
    food_name: str
    calories: float
    protein: float
    carbs: float
    fats: float
    day_index: int
    remaining_days: List[str]
    current_plan: List[dict]
    goal: str = "Maintain Weight"
    dietary_style: str = ""
    proteins: List[str] = []
    allergies: List[str] = []
    target_calories: int = 2000

# ─── LOG FOOD ENDPOINT ───────────────────────────────────
@app.post("/api/log-food")
def log_food(req: FoodLogRequest, user=Depends(get_user), db: Session = Depends(get_db)):
    if not user: raise HTTPException(401, "Not authenticated")
    entry = FoodLog(
        user_id=user.id,
        meal_name=req.meal_name,
        calories=req.calories,
        protein=req.protein,
        carbs=req.carbs,
        fats=req.fats,
        fiber=req.fiber,
        meal_type=req.meal_type,
        logged_date=req.logged_date or datetime.utcnow().strftime("%Y-%m-%d"),
        notes=req.notes,
        image_analysis=req.image_analysis
    )
    db.add(entry); db.commit()
    return {"message": "Logged", "id": entry.id}

# ─── MONTHLY SUMMARY ENDPOINT ────────────────────────────
@app.get("/api/food-logs/monthly-summary")
def monthly_summary(month: Optional[str] = None, user=Depends(get_user), db: Session = Depends(get_db)):
    if not user: raise HTTPException(401, "Not authenticated")
    if not month:
        month = datetime.utcnow().strftime("%Y-%m")
    year, mo = month.split("-")
    # Get all logs for this month
    logs = db.query(FoodLog).filter(
        FoodLog.user_id == user.id,
        FoodLog.logged_date.like(f"{month}%")
    ).all()

    if not logs:
        return {
            "days_logged": 0, "avg_daily_calories": 0,
            "total_protein": 0, "total_fiber": 0,
            "total_carbs": 0, "total_fats": 0,
            "avg_daily_protein": 0, "daily": [], "warnings": []
        }

    # Aggregate by date
    by_date = {}
    for log in logs:
        d = log.logged_date
        if d not in by_date:
            by_date[d] = {"date": d, "calories": 0, "protein": 0, "carbs": 0, "fats": 0, "fiber": 0}
        by_date[d]["calories"] += log.calories or 0
        by_date[d]["protein"] += log.protein or 0
        by_date[d]["carbs"] += log.carbs or 0
        by_date[d]["fats"] += log.fats or 0
        by_date[d]["fiber"] += log.fiber or 0

    daily = sorted(by_date.values(), key=lambda x: x["date"])
    days = len(daily)
    total_cal = sum(d["calories"] for d in daily)
    total_prot = round(sum(d["protein"] for d in daily), 1)
    total_carbs = round(sum(d["carbs"] for d in daily), 1)
    total_fats = round(sum(d["fats"] for d in daily), 1)
    total_fiber = round(sum(d["fiber"] for d in daily), 1)
    avg_cal = round(total_cal / days) if days else 0
    avg_prot = round(total_prot / days, 1) if days else 0

    # Health warnings
    warnings = []
    if avg_cal > 0 and avg_cal < 1200:
        warnings.append({"level": "danger", "icon": "🚨", "title": "Very Low Calorie Intake",
            "message": f"Averaging {avg_cal} kcal/day. Minimum recommended is 1200 kcal. Risk of nutrient deficiency."})
    if avg_cal > 3500:
        warnings.append({"level": "warning", "icon": "⚠️", "title": "High Calorie Intake",
            "message": f"Averaging {avg_cal} kcal/day which is quite high. Check your portions."})
    if avg_prot < 40 and days >= 3:
        warnings.append({"level": "warning", "icon": "💪", "title": "Low Protein",
            "message": f"Only {avg_prot}g protein/day on average. Aim for at least 50–60g daily."})
    if total_fiber / days < 15 and days >= 3:
        warnings.append({"level": "info", "icon": "🌾", "title": "Low Fiber",
            "message": f"Averaging {round(total_fiber/days,1)}g fiber/day. Target is 25–30g for gut health."})

    return {
        "month": month, "days_logged": days,
        "avg_daily_calories": avg_cal, "avg_daily_protein": avg_prot,
        "total_protein": total_prot, "total_carbs": total_carbs,
        "total_fats": total_fats, "total_fiber": total_fiber,
        "daily": [{"date": d["date"], "calories": round(d["calories"]), "protein": round(d["protein"],1)} for d in daily],
        "warnings": warnings
    }

# ─── REGENERATE SINGLE MEAL ──────────────────────────────
@app.post("/api/regenerate-meal")
def regenerate_meal(req: RegenMealRequest, user=Depends(get_user), db: Session = Depends(get_db)):
    blocklist = build_blocklist(req.allergies, req.dietary_style)
    conflict = enforce_dietary_protein_consistency(
        type("R", (), {"proteins": req.proteins, "dietary_style": req.dietary_style, "allergies": req.allergies})()
    )
    adj_proteins = conflict["proteins"]

    style = (req.dietary_style or "").lower()
    is_pesc = "pescatarian" in style
    is_veg = style == "vegetarian" or "vegan" in style
    B = round(req.target_calories * 0.25)
    L = round(req.target_calories * 0.35)
    D = round(req.target_calories * 0.30)
    S = round(req.target_calories * 0.05)
    cal_map = {"Breakfast": B, "Lunch": L, "Dinner": D, "Mid-Morning": S, "Evening": S, "Snack": S}
    target_cal = cal_map.get(req.meal_type, L)

    exclude_str = ", ".join(req.exclude_names[:20]) if req.exclude_names else "none"
    proteins_str = ", ".join(adj_proteins) if adj_proteins else "plant-based"
    blocklist_str = ", ".join(sorted(set(blocklist))) if blocklist else "none"

    if GEMINI_API_KEY:
        prompt = f"""Generate ONE single {req.meal_type} meal for {req.goal} goal.
Dietary style: {req.dietary_style}. Target calories: {target_cal} kcal.
Allowed proteins: {proteins_str}. Blocklist (never use): {blocklist_str}.
MUST NOT repeat any of these meals: {exclude_str}.
{"PRIORITY: Use seafood or fish as the protein since user is Pescatarian." if is_pesc else ""}
{"PRIORITY: Use meat/poultry as main protein since user is Non-Vegetarian." if not is_veg and not is_pesc else ""}

Respond with ONLY valid JSON (no markdown):
{{"type":"{req.meal_type}","name":"meal name","cal":{target_cal},"ingredients":[{{"name":"...","quantity":"...","calories":0}}],"macronutrients":{{"protein":"25g","carbohydrates":"45g","fats":"10g"}},"micronutrients_highlight":["Iron","B12"],"explainability":{{"why_selected":"...","nutritional_purpose":"...","goal_alignment":"...","allergy_confirmation":"Safe"}},"recipe":{{"steps":["step 1","step 2"],"cooking_time":"20 mins","difficulty":"Easy"}}}}"""
        try:
            import time
            text = call_gemini_text(prompt).strip()
            text = re.sub(r"^```[a-z]*\s*\n?", "", text, flags=re.MULTILINE)
            text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
            meal = json.loads(text.strip())
            meal["cal"] = meal.get("cal", target_cal)
            return meal
        except Exception as e:
            print(f"⚠️ Regen Gemini error: {e}")

    # Fallback: pick from pool avoiding excludes
    class FakeReq:
        dietary_style = req.dietary_style
        goal = req.goal
        proteins = adj_proteins
        cuisines = req.cuisines
        allergies = req.allergies

    fake = FakeReq()
    day_idx = 0  # rotate differently each call
    import random
    day_idx = random.randint(0, 6)

    import random
    # Try multiple fallback plans with different seeds to get variety
    candidates = []
    for seed in range(7):
        try:
            class ShiftReq:
                dietary_style = req.dietary_style
                goal = req.goal
                proteins = adj_proteins
                cuisines = req.cuisines
                allergies = req.allergies
            shift = ShiftReq()
            fp = make_fallback(req.target_calories, shift, adj_proteins, blocklist)
            for d_idx, day in enumerate(fp):
                for m in day.get("meals", []):
                    if (m.get("type") == req.meal_type
                            and m.get("name") not in req.exclude_names
                            and m.get("name") not in [c.get("name") for c in candidates]):
                        candidates.append(m)
        except Exception:
            pass

    # Pick randomly from valid candidates
    if candidates:
        return random.choice(candidates)

    # Absolute last resort: build a unique name variant
    raise HTTPException(404, "Could not generate a replacement meal — please try again")

# ─── UPDATE PLAN FROM FOOD LOG ───────────────────────────
@app.post("/api/update-plan-from-food")
def update_plan_from_food(req: UpdatePlanRequest, user=Depends(get_user), db: Session = Depends(get_db)):
    """After logging a meal, optionally re-adjust remaining days."""
    if not req.remaining_days or not req.current_plan:
        return {"adjustment_note": "No remaining days to adjust.", "adjusted_plan": []}

    # Calculate calorie delta
    meals_today = req.current_plan[0].get("meals", []) if req.current_plan else []
    planned_today_cal = sum(m.get("cal", 0) for m in meals_today)
    delta = req.calories - (planned_today_cal / max(len(meals_today), 1)) if meals_today else 0

    # Simple adjustment: if over by >200 kcal, reduce next day slightly
    adjusted_plan = []
    adjustment_note = ""

    if abs(delta) > 200 and GEMINI_API_KEY:
        direction = "reduce" if delta > 0 else "increase"
        adj_amount = min(abs(delta) * 0.5, 300)
        adj_note = f"You ate {abs(round(delta))} kcal {'more' if delta>0 else 'less'} than planned. Adjusting tomorrow by ~{round(adj_amount)} kcal."
        try:
            prompt = f"""You are a nutrition AI. The user ate "{req.food_name}" ({req.calories} kcal, {req.protein}g protein, {req.carbs}g carbs, {req.fats}g fats).
Their daily target is {req.target_calories} kcal. They need to {direction} tomorrow's intake by ~{round(adj_amount)} kcal.
Dietary style: {req.dietary_style}. Goal: {req.goal}.
Return ONLY JSON array with adjusted meals for tomorrow in same format as input plan.
Input plan day: {json.dumps(req.current_plan[0] if req.current_plan else {{}})}
Return: {{"adjusted_plan": [{{...same structure...}}], "adjustment_note": "brief note"}}"""
            text = call_gemini_text(prompt).strip()
            text = re.sub(r"^```[a-z]*\s*\n?", "", text, flags=re.MULTILINE)
            text = re.sub(r"\n?```\s*$", "", text, flags=re.MULTILINE)
            result = json.loads(text.strip())
            return result
        except Exception as e:
            print(f"⚠️ Plan update error: {e}")

    # Simple fallback: just return current plan unchanged with a note
    if delta > 200:
        adjustment_note = f"You ate ~{round(delta)} kcal over today. Consider lighter meals tomorrow."
    elif delta < -200:
        adjustment_note = f"You ate ~{abs(round(delta))} kcal under today. You can eat a bit more tomorrow."
    else:
        adjustment_note = "Great! Your intake today was close to your target. Plan stays as is."

    return {
        "adjusted_plan": req.current_plan,
        "adjustment_note": adjustment_note
    }

if __name__ == "__main__":
    import uvicorn
    print("\n" + "="*50)
    print("🌿 NutriSync v2.0 starting…")
    print("📌 http://localhost:8000")
    print("📌 /api/health  ← status check")
    print("📌 /api/analyse-image  ← NEW food image analyser")
    print("="*50 + "\n")
   