from flask import Flask, render_template, request
from dotenv import load_dotenv
from groq import Groq
from datetime import date, datetime, timedelta
import os
import json
import sqlite3

load_dotenv()

app = Flask(__name__)


# ==========================================
# DATABASE SETUP
# ==========================================

def init_db():

    conn = sqlite3.connect("studypilot.db")

    cursor = conn.cursor()

    # Study plans table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS study_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_date TEXT NOT NULL,
            days_remaining INTEGER NOT NULL,
            study_hours TEXT NOT NULL,
            syllabus TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # Progress table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            day_number INTEGER NOT NULL,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY (plan_id) REFERENCES study_plans(id)
        )
    """)

    conn.commit()
    conn.close()


# ==========================================
# GROQ CLIENT
# ==========================================

client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)


# ==========================================
# HOME PAGE
# ==========================================

@app.route("/")
def home():

    return render_template("index.html")


# ==========================================
# CREATE STUDY PLAN
# ==========================================

@app.route("/create-plan", methods=["POST"])
def create_plan():

    # Get data from form
    syllabus = request.form["syllabus"].strip()
    exam_date = request.form["exam_date"]
    study_hours = request.form["study_hours"]


    # ==========================================
    # DATE CALCULATION
    # ==========================================

    today = date.today()

    exam_day = datetime.strptime(
        exam_date,
        "%Y-%m-%d"
    ).date()

    days_remaining = (exam_day - today).days


    # ==========================================
    # VALIDATE EXAM DATE
    # ==========================================

    if days_remaining < 0:

        return """
        <h1>Invalid Exam Date ❌</h1>

        <p>
            Please select a future exam date.
        </p>

        <a href="/">
            Go Back
        </a>
        """


    # ==========================================
    # CREATE EXACT STUDY DATES
    # ==========================================

    study_dates = []

    for i in range(days_remaining):

        study_date = today + timedelta(days=i)

        study_dates.append(
            str(study_date)
        )


    # If exam is today
    if days_remaining == 0:

        study_dates = [
            str(today)
        ]


    # Convert dates into text
    available_dates = "\n".join(
        f"Day {i + 1}: {d}"
        for i, d in enumerate(study_dates)
    )


    # ==========================================
    # AI PROMPT
    # ==========================================

    prompt = f"""
You are StudyPilot, an AI-powered personal study planning assistant.

Create a realistic study plan for a college student.

TODAY:
{today}

EXAM DATE:
{exam_day}

DAYS AVAILABLE BEFORE EXAM:
{days_remaining}

EXACT STUDY DATES:

{available_dates}

IMPORTANT DATE RULES:

- Use only the dates provided above.
- Do not invent dates.
- Do not create dates before today.
- Do not create dates after the exam date.
- Each study day must have one day number.
- The number of plan days must match the number of available study dates.

STUDENT SYLLABUS:

{syllabus}

DAILY STUDY TIME:

{study_hours} hours

TASK:

Divide the syllabus into logical study topics.

A topic may take more than one day if necessary.

For every study day provide:

1. Main topic
2. Study time
3. Practice activity
4. Revision activity

Make the workload realistic.

IMPORTANT:

- Do not exceed the student's daily study time.
- Include practice questions.
- Include revision.
- Use the final available day mainly for revision when appropriate.
- Do not add topics that are not present in the syllabus.
- Do not create unnecessary duplicate topics.
- Return ONLY the requested structured JSON.
"""


    # ==========================================
    # JSON SCHEMA
    # ==========================================

    schema = {

        "type": "object",

        "properties": {

            "days": {

                "type": "array",

                "items": {

                    "type": "object",

                    "properties": {

                        "day": {
                            "type": "integer"
                        },

                        "date": {
                            "type": "string"
                        },

                        "topic": {
                            "type": "string"
                        },

                        "study_time": {
                            "type": "string"
                        },

                        "practice": {
                            "type": "string"
                        },

                        "revision": {
                            "type": "string"
                        }

                    },

                    "required": [
                        "day",
                        "date",
                        "topic",
                        "study_time",
                        "practice",
                        "revision"
                    ],

                    "additionalProperties": False
                }
            },

            "revision_strategy": {

                "type": "string"

            }

        },

        "required": [
            "days",
            "revision_strategy"
        ],

        "additionalProperties": False
    }


    # ==========================================
    # CALL GROQ
    # ==========================================

    response = client.chat.completions.create(

        model="openai/gpt-oss-20b",

        messages=[

            {
                "role": "system",

                "content":
                "You are a practical and accurate AI study planner. "
                "Always follow the requested JSON schema exactly."
            },

            {
                "role": "user",

                "content": prompt
            }

        ],

        # Give the model enough output space
        max_completion_tokens=4096,

        # Reduce unnecessary reasoning for this structured task
        reasoning_effort="low",

        response_format={

            "type": "json_schema",

            "json_schema": {

                "name": "study_plan",

                "strict": True,

                "schema": schema
            }
        }
    )


    # ==========================================
    # GET AI RESULT
    # ==========================================

    ai_result = response.choices[0].message.content


    # Safety check
    if not ai_result:

        return """
        <h1>AI Error ❌</h1>

        <p>
            StudyPilot did not receive a valid response from the AI.
        </p>

        <a href="/">
            Try Again
        </a>
        """


    # Convert JSON string into Python dictionary
    try:

        ai_plan = json.loads(ai_result)

    except json.JSONDecodeError:

        return """
        <h1>AI Response Error ❌</h1>

        <p>
            The AI returned an invalid study plan.
            Please try again.
        </p>

        <a href="/">
            Try Again
        </a>
        """


    # ==========================================
    # CREATE PLAN OBJECT
    # ==========================================

    plan = {

        "exam_date": str(exam_day),

        "days_remaining": days_remaining,

        "study_hours": study_hours,

        "days": ai_plan["days"],

        "revision_strategy":
        ai_plan["revision_strategy"]
    }


    # ==========================================
    # SAVE STUDY PLAN TO DATABASE
    # ==========================================

    conn = sqlite3.connect(
        "studypilot.db"
    )

    cursor = conn.cursor()


    # Insert study plan
    cursor.execute(
        """
        INSERT INTO study_plans
        (
            exam_date,
            days_remaining,
            study_hours,
            syllabus,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,

        (
            str(exam_day),
            days_remaining,
            study_hours,
            syllabus,
            str(datetime.now())
        )
    )


    # Get newly created plan ID
    plan_id = cursor.lastrowid


    # Create progress records
    for day in plan["days"]:

        cursor.execute(
            """
            INSERT INTO progress
            (
                plan_id,
                day_number,
                completed
            )
            VALUES (?, ?, ?)
            """,

            (
                plan_id,
                day["day"],
                0
            )
        )


    conn.commit()

    conn.close()


    # ==========================================
    # SEND PLAN TO HTML
    # ==========================================

    return render_template(
        "plan.html",
        plan=plan
    )


# ==========================================
# START APPLICATION
# ==========================================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True
    )