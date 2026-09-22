from flask import Flask, render_template, request
from dotenv import load_dotenv
from groq import Groq
from datetime import date, datetime, timedelta
import os
import json

load_dotenv()

app = Flask(__name__)

# Groq client
client = Groq(
    api_key=os.getenv("GROQ_API_KEY")
)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/create-plan", methods=["POST"])
def create_plan():

    # Get data from form
    syllabus = request.form["syllabus"]
    exam_date = request.form["exam_date"]
    study_hours = request.form["study_hours"]

    # Today's date
    today = date.today()

    # Convert exam date into date object
    exam_day = datetime.strptime(exam_date, "%Y-%m-%d").date()

    # Calculate days remaining
    days_remaining = (exam_day - today).days

    # Check invalid date
    if days_remaining < 0:
        return """
        <h1>Invalid Exam Date ❌</h1>
        <p>Please select a future exam date.</p>
        <a href="/">Go Back</a>
        """

    # Create exact dates available for studying
    study_dates = []

    for i in range(days_remaining):
        study_date = today + timedelta(days=i)
        study_dates.append(str(study_date))

    # If exam is tomorrow/today and there are no study days
    if days_remaining == 0:
        study_dates = [str(today)]

    # Convert dates into text for AI
    available_dates = "\n".join(
        f"Day {i + 1}: {d}"
        for i, d in enumerate(study_dates)
    )

    # AI prompt
    prompt = f"""
You are StudyPilot, an AI-powered personal study planning assistant.

Create a realistic study plan for a college student.

IMPORTANT DATE RULES:

Today's date: {today}
Exam date: {exam_day}
Days available before exam: {days_remaining}

The following are the EXACT dates available for study:

{available_dates}

You MUST use these dates.
Do not invent dates.
Do not create dates before today.
Do not create dates after the exam date.

STUDENT INFORMATION:

Syllabus:
{syllabus}

Daily available study time:
{study_hours} hours

TASK:

Break the syllabus into logical topics and distribute them across the available study days.

For every study day provide:

1. Main topic
2. Study time
3. Practice activity
4. Revision activity

Make the workload realistic.

Important:
- Do not exceed the student's daily study hours.
- Include practice questions.
- Include revision.
- Keep the final study day focused on revision if appropriate.
- Do not add topics that are not present in the syllabus.
"""


    # JSON schema for structured AI output
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


    # Ask Groq
    response = client.chat.completions.create(
        model="openai/gpt-oss-20b",

        messages=[
            {
                "role": "system",
                "content": "You are a practical and accurate AI study planner."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],

        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "study_plan",
                "strict": True,
                "schema": schema
            }
        }
    )


    # Get AI response
    ai_result = response.choices[0].message.content

    # Convert JSON string into Python dictionary
    ai_plan = json.loads(ai_result)


    # Add information calculated by Python
    plan = {
        "exam_date": str(exam_day),
        "days_remaining": days_remaining,
        "study_hours": study_hours,
        "days": ai_plan["days"],
        "revision_strategy": ai_plan["revision_strategy"]
    }


    # Send data to plan.html
    return render_template(
        "plan.html",
        plan=plan
    )


if __name__ == "__main__":
    app.run(debug=True)