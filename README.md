# 🚀 StudyPilot

### AI-Powered Personalized Study Planning & Adaptive Learning Platform

StudyPilot is an AI-powered learning platform designed to help students create personalized study plans, practice through AI-generated quizzes, identify weak topics, and adapt their preparation based on performance.

---

## ✨ Features

- 📚 **AI Study Plan**
  - Generate personalized study plans based on syllabus, exam date, and daily study hours.

- 🧠 **AI-Powered Quizzes**
  - Generate topic-specific MCQ quizzes using AI.
  - Get instant scores, correct answers, and explanations.

- 📊 **Quiz History**
  - Track previous quiz attempts and performance.

- 🎯 **Weak Topic Detection**
  - Automatically identify topics where performance is below the target level.

- 🔄 **Adaptive Study Plan**
  - Generate a revised study plan based on weak topics and quiz performance.

- 📝 **AI Handwritten Notes**
  - Generate concise exam-focused revision notes.
  - Download them as notebook-style PDF files.

- 📈 **Study Progress Tracking**
  - Track completed study-plan days.

- 🔐 **User Authentication**
  - Secure signup, login, and logout system.

---

## 🛠️ Tech Stack

### Frontend
- HTML5
- CSS3
- JavaScript

### Backend
- Python
- Flask

### Database
- SQLite

### AI
- Groq API

### Other Tools
- Git
- GitHub
- ReportLab

---

## 🏗️ Project Structure

```text
StudyPilot/
│
├── app.py
├── requirements.txt
├── .gitignore
│
└── templates/
    ├── index.html
    ├── plan.html
    ├── quiz.html
    ├── quiz_result.html
    ├── quiz_history.html
    ├── weak_topics.html
    ├── adaptive_plan.html
    ├── notes.html
    ├── signup.html
    └── login.html

---

## ⚙️ How to Run Locally

### 1. Clone the repository

```bash
git clone https://github.com/akashprasad001/Study-Pilot.git
cd Study-Pilot
```

### 2. Create a virtual environment

```bash
python -m venv venv
```

### 3. Activate the virtual environment

**Windows:**

```bash
venv\Scripts\activate
```

### 4. Install dependencies

```bash
pip install -r requirements.txt
```

### 5. Create `.env`

Create a `.env` file in the project root:

```env
GROQ_API_KEY=your_groq_api_key
SECRET_KEY=your_secret_key
```

> Never commit your `.env` file or API keys to GitHub.

### 6. Run the application

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

---

## 🧠 How StudyPilot Works

```text
Student
   ↓
Syllabus + Exam Date + Study Hours
   ↓
AI Study Plan
   ↓
Study & Track Progress
   ↓
AI Quiz
   ↓
Performance Analysis
   ↓
Weak Topic Detection
   ↓
Adaptive Study Plan
   ↓
Improved Preparation
```

---

## 🎯 Future Improvements

- 📄 PDF syllabus upload
- 📊 Advanced analytics dashboard
- ☁️ Cloud database
- 🌐 Production deployment
- 📱 Improved mobile experience
- 🔔 Study reminders and notifications
- 🤖 More advanced adaptive learning

---

## 👨‍💻 Author

**Akash Prasad**

B.Tech Computer Science & Engineering — AI & ML

GitHub: https://github.com/akashprasad001/Study-Pilot

---

## ⭐ Support

If you find StudyPilot useful, consider giving the repository a ⭐.
