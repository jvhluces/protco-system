# School Safety System — ProtCo

**Course / Block:** BSIT-3E<br>
**Project Name:**
ProtCo: An AI-Assisted Web-Based Spatial Decision Support System for School Infrastructure Vulnerability Assessment and Disaster Recovery Planning at Cawayan Elementary School<br>
**Framework:** Django (Python)<br>
**Database:** PostgreSQL<br>

## Members/ Authors
- Araya, Lovely Jane
- Aspa, Natasha Lorraine
- Luces, Janine
- Talaogon, Anthony

## Description

The **ProtCo System**  helps in the early detection of damage to school facilities. Using YOLOv8, it can identify whether the detected damage is Low, Medium, or High Risk. After that, Gemini AI will provide explanations and short-term solutions. It also includes a school map, weather forecast, and SMS notifications so reports can be sent immediately to the local government unit for quick action.


### Working Features:
- **Damage Detection** — Uploading photos and identifying infrastructure damage using the YOLOv8 model from Roboflow
- **Risk Assessment** —Automatic identification of whether the detected damage is Low, Medium, or High Risk based on the detection confidence score
- **AI Suggestions** —Using Google Gemini AI, it gives an explanation about the damage seen and suggestions for temporary fixes or precautions
-  **Weather Forecast** — Real-time information about the weather using the OpenWeather API
- 
### ⏳ Ongoing Development:
-  Interactive School Map
- SMS Notification System para sa LGU reports
- Other system improvements

---

## Dependencies
Before running the system, make sure the following are installed on your computer:
- **Python 3.10+**
- **PostgreSQL 14+**
- **pip** (Python package manager)


### Python Packages You Need to Install:
Run this in the terminal inside the project folder:

```bash
pip install django psycopg2-binary python-dotenv requests pillow onnxruntime opencv-python




