from flask import Flask, render_template, request, jsonify
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

def get_db():

    conn = sqlite3.connect("studypilot.db")

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

    cursor = conn.cursor()

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL,
            day_number INTEGER NOT NULL,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY (plan_id) REFERENCES study_plans(id),
            UNIQUE(plan_id, day_number)
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

        <p>Please select a future exam date.</p>

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

        max_completion_tokens=4096,

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


    if not ai_result:

        return """
        <h1>AI Error ❌</h1>

        <p>
            StudyPilot did not receive a valid response.
        </p>

        <a href="/">
            Try Again
        </a>
        """


    try:

        ai_plan = json.loads(ai_result)

    except json.JSONDecodeError:

        return """
        <h1>AI Response Error ❌</h1>

        <p>
            The AI returned an invalid study plan.
        </p>

        <a href="/">
            Try Again
        </a>
        """


    # ==========================================
    # SAVE STUDY PLAN
    # ==========================================

    conn = get_db()

    cursor = conn.cursor()

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


    plan_id = cursor.lastrowid


    # ==========================================
    # CREATE PROGRESS RECORDS
    # ==========================================

    for day in ai_plan["days"]:

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


    # ==========================================
    # GET CURRENT COMPLETED DAYS
    # ==========================================

    cursor.execute(
        """
        SELECT day_number
        FROM progress
        WHERE plan_id = ?
        AND completed = 1
        """,
        (plan_id,)
    )

    completed_days = [
        row["day_number"]
        for row in cursor.fetchall()
    ]


    conn.close()


    # ==========================================
    # CREATE PLAN OBJECT
    # ==========================================

    plan = {

        "id": plan_id,

        "exam_date": str(exam_day),

        "days_remaining": days_remaining,

        "study_hours": study_hours,

        "days": ai_plan["days"],

        "revision_strategy":
        ai_plan["revision_strategy"],

        "completed_days":
        completed_days
    }


    # ==========================================
    # SEND TO HTML
    # ==========================================

    return render_template(
        "plan.html",
        plan=plan
    )


# ==========================================
# UPDATE PROGRESS
# ==========================================

@app.route("/toggle-progress", methods=["POST"])
def toggle_progress():

    data = request.get_json()

    plan_id = data.get("plan_id")

    day_number = data.get("day_number")

    completed = data.get("completed")


    # Validate data
    if plan_id is None or day_number is None or completed is None:

        return jsonify({
            "success": False,
            "message": "Invalid request data."
        }), 400


    conn = get_db()

    cursor = conn.cursor()


    # Check if progress record exists
    cursor.execute(
        """
        SELECT id
        FROM progress
        WHERE plan_id = ?
        AND day_number = ?
        """,
        (
            plan_id,
            day_number
        )
    )

    record = cursor.fetchone()


    if record is None:

        conn.close()

        return jsonify({
            "success": False,
            "message": "Progress record not found."
        }), 404


    # Update completion status
    cursor.execute(
        """
        UPDATE progress
        SET completed = ?
        WHERE plan_id = ?
        AND day_number = ?
        """,
        (
            1 if completed else 0,
            plan_id,
            day_number
        )
    )


    conn.commit()


    # Get updated progress count
    cursor.execute(
        """
        SELECT COUNT(*) AS completed_count
        FROM progress
        WHERE plan_id = ?
        AND completed = 1
        """,
        (plan_id,)
    )

    completed_count = cursor.fetchone()["completed_count"]


    cursor.execute(
        """
        SELECT COUNT(*) AS total_count
        FROM progress
        WHERE plan_id = ?
        """,
        (plan_id,)
    )

    total_count = cursor.fetchone()["total_count"]


    conn.close()


    # Calculate percentage
    if total_count == 0:

        percentage = 0

    else:

        percentage = (
            completed_count / total_count
        ) * 100


    return jsonify({

        "success": True,

        "completed_count": completed_count,

        "total_count": total_count,

        "percentage": percentage

    })


# ==========================================
# START APPLICATION
# ==========================================

if __name__ == "__main__":

    init_db()

    app.run(
        debug=True
    )