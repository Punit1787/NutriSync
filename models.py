from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, ForeignKey, Float
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

DATABASE_URL = "sqlite:///./nutrisync.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200), unique=True, index=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    token = Column(String(256), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class MealPlan(Base):
    __tablename__ = "meal_plans"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    goal = Column(String(100))
    target_calories = Column(Integer)
    bmr = Column(Integer)
    tdee = Column(Integer)
    plan_json = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class FoodLog(Base):
    __tablename__ = "food_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    meal_name = Column(String(300), nullable=False)
    calories = Column(Float, default=0)
    protein = Column(Float, default=0)
    carbs = Column(Float, default=0)
    fats = Column(Float, default=0)
    fiber = Column(Float, default=0)
    meal_type = Column(String(50), default="Meal")
    logged_date = Column(String(20))  # YYYY-MM-DD
    notes = Column(Text, nullable=True)
    image_analysis = Column(Text, nullable=True)  # JSON from image analysis
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)
    print("✅ Database ready")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()