from flask import Flask, render_template
from dotenv import load_dotenv
import os
load_dotenv()

app = Flask(__name__)


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/create-plan", methods=["POST"])
def create_plan():

    syllabus = request.form["syllabus"]
    exam_date = request.form["exam_date"]
    study_hours = request.form["study_hours"]

    return f"""
    <h1>Study Plan Request Received ✅</h1>

    <h3>Syllabus:</h3>
    <p>{syllabus}</p>

    <h3>Exam Date:</h3>
    <p>{exam_date}</p>

    <h3>Daily Study Hours:</h3>
    <p>{study_hours} hours</p>
    """


if __name__ == "__main__":
    app.run(debug=True)