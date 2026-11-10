import base64
import json
import logging
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ultralytics import YOLO

from .models import DetectionResult

from django.shortcuts import render
from django.conf import settings
# Kung mayroon kang function na nagkuha ng weather — i-import mo rin
# from .utils import get_current_weather, get_weather_forecast

logger = logging.getLogger(__name__)


# =====================================================================
# WEATHER
# =====================================================================
def _fetch_openweather(endpoint, location):
    if not settings.OPENWEATHER_API_KEY:
        return None, "Set OPENWEATHER_API_KEY in your environment and restart Django."

    params = urlencode(
        {
            "q": location,
            "appid": settings.OPENWEATHER_API_KEY,
            "units": "metric",
        }
    )
    url = f"https://api.openweathermap.org/data/2.5/{endpoint}?{params}"

    try:
        with urlopen(url, timeout=5) as response:
            data = json.loads(response.read())
    except HTTPError as exc:
        logger.warning("OpenWeather returned HTTP status %s.", exc.code)
        if exc.code in (401, 403):
            return None, "OpenWeather rejected the API key. Check that it is active."
        if exc.code == 404:
            return None, "OpenWeather could not find the configured city."
        return None, f"OpenWeather request failed (HTTP {exc.code})."
    except (URLError, TimeoutError):
        logger.warning("Could not connect to OpenWeather.")
        return None, "Could not connect to OpenWeather. Try again later."
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("OpenWeather returned invalid JSON.")
        return None, "OpenWeather returned an invalid response."

    return data, None


def _safe_icon(icon):
    if len(icon) == 3 and icon[:2].isdigit() and icon[2] in "dn":
        return icon
    return "01d"


def get_current_weather(location=None):
    location = location or getattr(settings, "OPENWEATHER_CITY", "Manito,PH")
    data, error = _fetch_openweather("weather", location)
    if error:
        return None, error

    try:
        weather = data["weather"][0]
        return {
            "temp": round(data["main"]["temp"]),
            "description": weather["description"].capitalize(),
            "city": f"{data['name']}, {data['sys']['country']}",
            "humidity": data["main"]["humidity"],
            "wind_kmh": round(data["wind"]["speed"] * 3.6),
            "icon": _safe_icon(weather["icon"]),
        }, None
    except (IndexError, KeyError, TypeError, ValueError):
        logger.warning("OpenWeather response is missing expected weather fields.")
        return None, "OpenWeather returned incomplete weather data."


def get_weather_forecast(location):
    data, error = _fetch_openweather("forecast", location)
    if error:
        return None, error

    try:
        timezone_offset = data["city"]["timezone"]
        daily = {}
        for entry in data["list"]:
            local_time = datetime.fromtimestamp(
                entry["dt"] + timezone_offset, tz=timezone.utc
            )
            day = local_time.date()
            conditions = entry["weather"][0]
            forecast = daily.setdefault(
                day,
                {
                    "date": local_time.strftime("%a, %b %d").replace(" 0", " "),
                    "temp_min": entry["main"]["temp_min"],
                    "temp_max": entry["main"]["temp_max"],
                    "description": conditions["description"].capitalize(),
                    "icon": _safe_icon(conditions["icon"]),
                    "noon_distance": float("inf"),
                },
            )
            forecast["temp_min"] = min(forecast["temp_min"], entry["main"]["temp_min"])
            forecast["temp_max"] = max(forecast["temp_max"], entry["main"]["temp_max"])

            noon_distance = abs((local_time.hour + local_time.minute / 60) - 12)
            if noon_distance < forecast["noon_distance"]:
                forecast["description"] = conditions["description"].capitalize()
                forecast["icon"] = _safe_icon(conditions["icon"])
                forecast["noon_distance"] = noon_distance

        return [
            {k: v for k, v in forecast.items() if k != "noon_distance"}
            for _, forecast in sorted(daily.items())[:5]
        ], None
    except (IndexError, KeyError, TypeError, ValueError, OverflowError):
        logger.warning("OpenWeather forecast response is missing expected fields.")
        return None, "OpenWeather returned incomplete forecast data."


def weather_forecast(request):
    # Kunin muna ang galing sa search bar, kung wala → default
    location = request.GET.get("location") or getattr(settings, "OPENWEATHER_CITY", "Manito,PH")
    
    weather, weather_error = get_current_weather(location)
    forecast, forecast_error = get_weather_forecast(location)
    
    return render(request, "detection/weather_forecast.html", {
        "weather": weather,
        "forecast": forecast or [],
        "weather_error": weather_error,
        "forecast_error": forecast_error,
        "location": location,  # ← Ipakita sa input box ang hinanap
    })

def damage_check(request):
    # Kunin mula sa search bar, kung wala → gamitin ang default
    location = request.GET.get("location") or getattr(settings, "OPENWEATHER_CITY", "Manito,PH")
    
    weather, weather_error = get_current_weather(location)
    
    return render(request, "detection/damage_check.html", {
        "weather": weather,
        "weather_error": weather_error,
        "location": location,  # ← Ipakita sa input box
    })



# =====================================================================
# YOLO MODEL
# =====================================================================
MODEL_CANDIDATES = [
    Path(settings.BASE_DIR) / "best.pt",
    Path(settings.BASE_DIR) / "detection" / "best.pt",
    Path.cwd() / "best.pt",
]

MODEL_PATH = next(
    (path.resolve() for path in MODEL_CANDIDATES if path.is_file()),
    None,
)

model = None

if MODEL_PATH:
    try:
        model = YOLO(str(MODEL_PATH))
        logger.info("ProtCo YOLO model loaded from: %s", MODEL_PATH)
    except Exception:
        logger.exception("Failed to load YOLO model from %s", MODEL_PATH)
else:
    logger.error(
        "YOLO model not found. Place best.pt beside manage.py "
        "or inside the detection/ folder. Checked paths:\n%s",
        "\n".join(str(path) for path in MODEL_CANDIDATES),
    )

CONFIDENCE_THRESHOLD = 0.15
IMAGE_SIZE = 1024
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

NO_DAMAGE_LABELS = {
    "No Confirmed Damage Detected",
    "Object Detected - Damage Not Confirmed",
}

DAMAGE_MAP = {
    "crack": "Potential Wall / Surface Crack",
    "cracks": "Potential Wall / Surface Crack",
    "wall crack": "Potential Wall / Surface Crack",
    "concrete crack": "Potential Concrete Crack",
    "structural crack": "Potential Structural Crack",
    "damaged wall": "Potential Wall Damage",
    "wall damage": "Potential Wall Damage",
    "structural damage": "Potential Structural Damage",
    "broken glass": "Potential Broken Glass",
    "broken window": "Potential Window Damage",
    "damaged window": "Potential Window Damage",
    "damaged door": "Potential Door Damage",
    "damaged chair": "Potential Furniture Damage",
    "damaged bench": "Potential Furniture Damage",
    "damage": "Potential Infrastructure Damage",
}


def normalize_label(label):
    return " ".join(str(label).strip().lower().replace("_", " ").split())


def classify_detections(result):
    """Return (damage summary, best confidence, all detected labels)."""
    detected_labels = []
    damage_candidates = []

    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return "No Confirmed Damage Detected", 0.0, detected_labels

    names = result.names
    for box in boxes:
        cls_id = int(box.cls[0].item())
        confidence = float(box.conf[0].item())

        if isinstance(names, dict):
            raw_label = str(names.get(cls_id, f"class_{cls_id}"))
        else:
            raw_label = str(names[cls_id])

        detected_labels.append(f"{raw_label} {confidence:.0%}")

        mapped = DAMAGE_MAP.get(normalize_label(raw_label))
        if mapped:
            damage_candidates.append((confidence, mapped, raw_label))

    if not damage_candidates:
        return "Object Detected - Damage Not Confirmed", 0.0, detected_labels

    damage_candidates.sort(key=lambda item: item[0], reverse=True)
    best_confidence, best_damage, _ = damage_candidates[0]

    if len(damage_candidates) > 1:
        damage_found = (
            f"{best_damage} (Additional Potential Damage: "
            f"{len(damage_candidates) - 1} more detection(s))"
        )
    else:
        damage_found = best_damage

    return damage_found, best_confidence, detected_labels


def get_damage_category(damage_name):
    name = normalize_label(damage_name)
    categories = {
        "crack": ["crack", "fracture", "fissure"],
        "window": ["window", "broken glass", "glass"],
        "electrical": ["electrical", "wiring", "wire", "outlet", "exposed cable"],
        "water": ["water leak", "leak", "water damage", "dampness", "mold"],
        "ceiling": ["ceiling", "roof", "collapse"],
        "floor": ["floor", "stairs", "walkway", "tile"],
        "furniture": ["chair", "desk", "table", "bench", "furniture"],
        "wall": ["wall damage", "hole in wall"],
        "door": ["door"],
    }
    for category, keywords in categories.items():
        if any(keyword in name for keyword in keywords):
            return category
    return "general"


def count_previous_similar_reports(latest, reports):
    if latest is None:
        return 0
    current_category = get_damage_category(latest.damage_type or "")
    if current_category == "general":
        return 0
    return sum(
        1
        for previous in reports
        if previous.pk != latest.pk
        and get_damage_category(previous.damage_type or "") == current_category
    )


def _gemini_image_analysis(image_path, yolo_damage, yolo_confidence, detected_labels):
    api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
    if not api_key:
        return {
            "available": False,
            "error": "Gemini API key is not configured. Add GEMINI_API_KEY to settings/environment.",
        }

    try:
        mime_type = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
        if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            mime_type = "image/jpeg"

        image_bytes = Path(image_path).read_bytes()
        prompt = f"""
You are ProtCo, an AI visual assistant for school infrastructure safety.
Analyze the attached image carefully. Do not invent details that are not visible.

YOLO detector result (authoritative for damage detection):
- YOLO damage label: {yolo_damage}
- YOLO damage confidence: {yolo_confidence:.0%}
- YOLO detected labels: {", ".join(detected_labels) if detected_labels else "None"}

STRICT RULES:
1. YOLO is authoritative for whether a damage class was detected and for its confidence.
2. Never change, estimate, round, or replace the YOLO confidence. Do not output another confidence percentage.
3. If YOLO damage label is "No Confirmed Damage Detected" or "Object Detected - Damage Not Confirmed", treat damage as NOT CONFIRMED. Describe visible objects/scenery only, say no damage was confirmed by YOLO, and set risk_level to "Low". Do not claim that a person/object is damage.
4. If YOLO detected a recognized damage class, discuss that detected damage while distinguishing visible evidence from uncertainty.
5. Risk level must be exactly Low, Medium, or High. Base it on visible severity and immediate safety implications, not on YOLO confidence alone.
6. This is preliminary visual guidance, not a structural engineering diagnosis. Never claim certainty about hidden structural conditions.
7. Recommendations must be practical and safe for a school environment. Do not advise untrained people to repair electrical or structural hazards.

Return ONLY valid JSON with exactly these keys:
{{
  "image_description": "A concise description of what is visibly present in the image.",
  "damage_assessment": "Explain whether damage is confirmed by YOLO and what the image suggests.",
  "risk_level": "Low, Medium, or High",
  "immediate_actions": ["Action 1", "Action 2"],
  "avoid_actions": ["Thing to avoid 1"],
  "maintenance_priority": "A short priority statement and who should inspect it.",
  "visual_observations": ["Visible observation 1", "Visible observation 2"]
}}
Use 2-5 useful list items where appropriate. Keep the language clear and specific.
"""
        payload = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {
                        "mime_type": mime_type,
                        "data": base64.b64encode(image_bytes).decode("ascii"),
                    }},
                ]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
                "maxOutputTokens": 1200,
            },
        }

        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash").strip()
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model_name}:generateContent"
        )
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST",
        )
        with urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))

        parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        response_text = next(
            (part.get("text", "") for part in parts if part.get("text")), ""
        )
        if not response_text:
            raise ValueError("Gemini returned an empty response.")

        cleaned = response_text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        analysis = json.loads(cleaned)
        risk = str(analysis.get("risk_level", "Low")).strip().title()
        if risk not in {"Low", "Medium", "High"}:
            risk = "Low"

        def clean_text(value, fallback):
            value = str(value or "").strip()
            return value[:1800] if value else fallback

        def clean_list(value, fallback):
            if not isinstance(value, list):
                value = []
            items = [str(i).strip()[:500] for i in value if str(i).strip()]
            return items[:6] or fallback

        no_damage = yolo_confidence <= 0 or yolo_damage in NO_DAMAGE_LABELS
        if no_damage:
            risk = "Low"
            analysis["damage_assessment"] = (
                "YOLO did not confirm any recognized infrastructure damage. "
                + clean_text(
                    analysis.get("damage_assessment"),
                    "The image may contain ordinary objects or people, but no damage was confirmed.",
                )
            )

        return {
            "available": True,
            "image_description": clean_text(
                analysis.get("image_description"),
                "Gemini could not provide a detailed image description.",
            ),
            "damage_assessment": clean_text(
                analysis.get("damage_assessment"),
                "No detailed damage assessment was returned.",
            ),
            "risk_level": risk,
            "immediate_actions": clean_list(
                analysis.get("immediate_actions"),
                ["Have school maintenance staff review the image."],
            ),
            "avoid_actions": clean_list(
                analysis.get("avoid_actions"),
                ["Do not attempt repairs without the appropriate qualified personnel."],
            ),
            "maintenance_priority": clean_text(
                analysis.get("maintenance_priority"),
                "Record the observation and request a maintenance inspection if needed.",
            ),
            "visual_observations": clean_list(
                analysis.get("visual_observations"),
                ["Review the uploaded image for visible details."],
            ),
        }

    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:500]
        logger.warning("Gemini API returned HTTP %s: %s", exc.code, detail)
        return {
            "available": False,
            "error": f"Gemini API request failed (HTTP {exc.code}). Check the API key, model, and API access.",
        }
    except (URLError, TimeoutError):
        logger.exception("Could not connect to Gemini API.")
        return {
            "available": False,
            "error": "Could not connect to Gemini. Check your internet connection and try again.",
        }
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError):
        logger.exception("Gemini returned an invalid response.")
        return {
            "available": False,
            "error": "Gemini returned an invalid response. Please try another image.",
        }
    except Exception:
        logger.exception("Gemini image analysis failed.")
        return {
            "available": False,
            "error": "Gemini analysis failed. Check the Django terminal for details.",
        }


ALLOWED_DAMAGE_KEYWORDS = [
    "crack", "spall", "peeling", "paint", "rust", "corrosion", "broken",
    "damage", "dent", "hole", "leak", "loose", "collapse", "tilt", "sagging",
    "foundation", "wall", "ceiling", "floor", "roof", "beam", "column",
    "door", "window", "pipe", "wire", "electric", "fence", "gate",
    "stair", "railing", "handrail", "light", "fixture", "sign", "pavement",
    "drain", "concrete", "masonry", "structural",
]

FORBIDDEN_CONTENT = [
    "person", "people", "human", "face", "animal", "dog", "cat", "bird",
    "tree", "plant", "flower", "sky", "cloud", "sun", "moon", "car",
    "vehicle", "bike", "motorcycle", "bicycle", "food", "drink", "furniture",
]


def _contains_word(text, words):
    """Buong salita lang ang titingnan (hindi 'cat' sa loob ng 'location')."""
    return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)


def _can_submit(latest):
    if not latest or not latest.damage_type:
        return False
    if latest.damage_type in NO_DAMAGE_LABELS or latest.damage_type.startswith("Detection"):
        return False
    if not latest.confidence or latest.confidence <= 0:
        return False

    text = latest.damage_type.lower()
    return _contains_word(text, ALLOWED_DAMAGE_KEYWORDS) and not _contains_word(
        text, FORBIDDEN_CONTENT
    )


def upload_and_detect(request):
    latest = None
    error_message = None
    gemini_analysis = None
    previous_similar_count = 0

    location = request.GET.get(
        "location", getattr(settings, "OPENWEATHER_CITY", "Manito,PH")
    )
    weather, weather_error = get_current_weather(location)

    resolver_match = getattr(request, "resolver_match", None)
    template_name = (
        "detection/damage_check.html"
        if resolver_match and resolver_match.url_name == "damage_check"
        else "detection/upload.html"
    )

    if request.method == "POST" and request.FILES.get("image"):
        img_file = request.FILES["image"]
        extension = Path(img_file.name).suffix.lower()

        if extension not in ALLOWED_EXTENSIONS:
            error_message = (
                "Unsupported file type. Please upload a JPG, JPEG, PNG, or WebP image."
            )
        else:
            report = DetectionResult.objects.create(image=img_file)

            try:
                if model is None:
                    report.damage_type = "Detection Unavailable - Model Not Loaded"
                    report.confidence = 0.0
                    report.save()
                    error_message = (
                        "The YOLO model could not be loaded. Place best.pt in the project "
                        "root or detection/ folder, then restart Django."
                    )
                else:
                    image_path = Path(report.image.path)
                    if not image_path.is_file():
                        raise FileNotFoundError(f"Uploaded image not found: {image_path}")

                    results = model.predict(
                        source=str(image_path),
                        conf=CONFIDENCE_THRESHOLD,
                        imgsz=IMAGE_SIZE,
                        verbose=False,
                    )
                    result = results[0]

                    detected_rel_path = f"detections/detected/{report.pk}_detected.jpg"
                    detected_abs_path = Path(settings.MEDIA_ROOT) / detected_rel_path
                    detected_abs_path.parent.mkdir(parents=True, exist_ok=True)
                    result.save(filename=str(detected_abs_path))

                    damage_found, confidence, detected_labels = classify_detections(result)

                    report.damage_type = damage_found
                    report.confidence = confidence
                    report.detected_image = detected_rel_path
                    report.save()

                    gemini_analysis = _gemini_image_analysis(
                        image_path=image_path,
                        yolo_damage=damage_found,
                        yolo_confidence=confidence,
                        detected_labels=detected_labels,
                    )

                    logger.info(
                        "Detection complete | ID:%s | damage:%s | confidence:%.3f | labels:%s",
                        report.pk, damage_found, confidence, detected_labels,
                    )

            except Exception:
                logger.exception("Detection failed for report ID %s", report.pk)
                report.damage_type = "Detection Failed - Check Server Logs"
                report.confidence = 0.0
                report.save()
                error_message = "Image analysis failed. Check the Django terminal for details."

            # Nasa labas ng try/except para laging may "latest" (success man o hindi)
            latest = report
            previous_similar_count = count_previous_similar_reports(
                latest, DetectionResult.objects.exclude(pk=latest.pk)
            )

    return render(
        request,
        template_name,
        {
            "latest": latest,
            "error_message": error_message,
            "gemini_analysis": gemini_analysis,
            "previous_similar_count": previous_similar_count,
            "weather": weather,
            "weather_error": weather_error,
            "can_submit_report": _can_submit(latest),
        },
    )


# =====================================================================
# REPORTS
# =====================================================================
def reports_list(request):
    all_reports = DetectionResult.objects.all().order_by("-created_at")

    reports_data = []
    for r in all_reports:
        damage = r.damage_type or ""
        if damage.startswith("Detection"):
            status_class, status_text = "failed", "Analysis Failed"
        elif damage in NO_DAMAGE_LABELS:
            status_class, status_text = "pending", "No Damage Found"
        else:
            status_class, status_text = "submitted", "Report Submitted"

        reports_data.append(
            {
                "id": r.id,
                "damage_type": damage or "Unspecified Damage",
                "location": getattr(r, "location", None) or "Manito, Albay",
                "created_at": r.created_at,
                "confidence": (r.confidence or 0) * 100,
                "image": r.image,
                "status_class": status_class,
                "status_text": status_text,
            }
        )

    return render(request, "detection/reports.html", {"reports": reports_data})


def _report_status(damage):
    damage = damage or ""
    if damage.startswith("Detection"):
        return "failed", "Analysis Failed"
    if damage in NO_DAMAGE_LABELS:
        return "pending", "No Damage Found"
    return "submitted", "Report Submitted"


def report_detail(request, report_id):
    report = get_object_or_404(DetectionResult, id=report_id)
    status_class, status_text = _report_status(report.damage_type)

    return render(
        request,
        "detection/report_detail.html",
        {
            "report": report,
            "status_class": status_class,
            "status_text": status_text,
            "confidence_pct": (report.confidence or 0) * 100,
            "location": getattr(report, "location", None) or "Manito, Albay",
        },
    )


@require_POST
def delete_report(request, report_id):
    """Remove the report from the database and its associated image files."""
    report = get_object_or_404(DetectionResult, id=report_id)
    report_pk = report.pk

    # Hindi awtomatikong binubura ng Django ang mga file, kaya manual natin
    for field in (report.image, report.detected_image):
        if field:
            try:
                field.delete(save=False)
            except Exception:
                logger.exception("Could not delete file for report %s", report_pk)

    report.delete()
    messages.success(request, f"Report #{report_pk} was deleted.")
    return redirect("detection:reports")


