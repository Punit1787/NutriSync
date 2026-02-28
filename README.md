# 🥗 NutriSync
**AI-Powered Nutrition and Diet Recommendation Platform**

[![Frontend Deployment](https://img.shields.io/badge/Vercel-Deployed-black?logo=vercel)](https://nutri-sync-alpha.vercel.app)
[![Backend API](https://img.shields.io/badge/Render-Live-success?logo=render)](https://nutrisync-vgjj.onrender.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111.0-009688?logo=fastapi)](https://fastapi.tiangolo.com/)

NutriSync is an adaptive, explainable, and goal-aware nutrition guidance engine. Moving beyond static calorie trackers, it leverages **Generative AI (Google Gemini)** and modern web technologies to dynamically generate and track meal plans based on personal health data, goals, and constraints.

**Live Demo:** [nutri-sync-alpha.vercel.app](https://nutri-sync-alpha.vercel.app)  

---

## 🏆 Hackathon Details
Built for **CSMIT CESA CODE : AUTOMATA VER. 2.1 (2026)**
* **Problem Statement:** AI-Powered Nutrition and Diet Recommendation Platform (HC-3)
* **College:** Vidyalankar Institute of Technology
* **Team Name:** Unknown
* **Team Members:** Ranveer, Harsh, Ujwal, Punit

---

## 💡 The Vision
Most nutrition apps live in a vacuum, relying on manual tracking and static diet plans that users abandon quickly. NutriSync breaks those walls by dynamically generating meal plans that are goal-oriented (loss, gain, maintenance) and preference-aware (vegetarian, allergies, cultural diets). It syncs real-world progress using a custom **FastAPI** backend and **SQLAlchemy**.

## 🚀 Key Features
- **Generative AI Meal Planning:** Uses Google's Gemini API to adapt diet plans instantly based on user conditions.
- **Automated Biometrics:** Computes BMI, BMR, and TDEE (Total Daily Energy Expenditure) to calculate exact caloric needs.
- **SQLAlchemy ORM:** Robust data modeling and persistence for user profiles, meal plans, and daily food logs.
- **Google OAuth Integration:** Secure, seamless one-tap login.
- **Minimalist Frontend:** A sleek, framework-less UI (HTML/CSS/JS) deployed on Vercel for maximum speed and zero bloat.

---

## 🛠️ Tech Stack

| Layer | Technology |
| :--- | :--- |
| **Frontend** | Vanilla HTML5, CSS3, JavaScript (ES6+), Vercel |
| **API Framework** | Python 3.14+, FastAPI, Uvicorn |
| **Database/ORM** | SQLite, SQLAlchemy |
| **AI Integration** | Google Gemini API |
| **Auth** | Google Identity Services (OAuth 2.0) |
| **Backend Deployment**| Render |

---

## ⚙️ Local Development

### 1. Prerequisites
* Python 3.14+
* Google Cloud Console Project (with OAuth Client ID configured)
* Google Gemini API Key

### 2. Setup
```bash
# Clone the repo
git clone [https://github.com/Punit1787/NutriSync.git](https://github.com/Punit1787/NutriSync.git)
cd NutriSync

# Setup virtual environment
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
