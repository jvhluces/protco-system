import base64
import json
import logging
import mimetypes
import re
from django.contrib.auth import logout
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.http import JsonResponse
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

import cv2
import numpy as np

from .models import DetectionResult

logger = logging.getLogger(__name__)


# =====================================================================
# ONNX MODEL (best.onnx) - LAZY LOAD
# =====================================================================
# Ilagay ang best.onnx sa tabi ng manage.py (o sa detection/ folder).
MODEL_CANDIDATES = [
    Path(settings.BASE_DIR) / "best.onnx",
    Path(settings.BASE_DIR) / "detection" / "best.onnx",
    Path.cwd() / "best.onnx",
]

MODEL_PATH = next(
    (path.resolve() for path in MODEL_CANDIDATES if path.is_file()),
    None,
)

# Dapat kapareho ng pagkakasunod ng classes noong na-train mo ang model.
CLASS_NAMES = {0: "Crack", 1: "Damage", 2: "Structural"}

CONFIDENCE_THRESHOLD = 0.15
NMS_THRESHOLD = 0.45
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

_session = None


def get_model():
    """I-load ang ONNX session isang beses lang (lazy), para hindi mag-error ang migrate/admin."""
    global _session
    if _session is not None:
        return _session
    if not MODEL_PATH:
        logger.error("best.onnx not found. Place it beside manage.py or inside detection/ folder.")
        return None
    try:
        import onnxruntime as ort
        _session = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
        logger.info("ProtCo ONNX model loaded from: %s", MODEL_PATH)
    except Exception:
        logger.exception("Failed to load ONNX model from %s", MODEL_PATH)
        return None
    return _session


def run_onnx_detection(image_path, save_path):
    """
    Mag-detect gamit ang best.onnx, gumuhit ng boxes, at i-save ang annotated image sa save_path.
    Return: [(label, confidence), ...]
    """
    session = get_model()
    if session is None:
        raise RuntimeError("ONNX model not loaded")

    meta = session.get_inputs()[0]
    in_h = meta.shape[2] if isinstance(meta.shape[2], int) else 640
    in_w = meta.shape[3] if isinstance(meta.shape[3], int) else 640

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"Cannot read image: {image_path}")
    orig_h, orig_w = img.shape[:2]

    # Letterbox (parehas ng ginagawa ng YOLO para hindi ma-stretch ang image)
    scale = min(in_w / orig_w, in_h / orig_h)
    new_w, new_h = int(round(orig_w * scale)), int(round(orig_h * scale))
    pad_x, pad_y = (in_w - new_w) // 2, (in_h - new_h) // 2
    canvas = np.full((in_h, in_w, 3), 114, dtype=np.uint8)
    canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = cv2.resize(img, (new_w, new_h))

    blob = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    blob = np.expand_dims(np.transpose(blob, (2, 0, 1)), 0)

    pred = session.run(None, {meta.name: blob})[0][0]
    if pred.shape[0] < pred.shape[1]:  # (4+nc, N) -> (N, 4+nc)
        pred = pred.T

    class_scores = pred[:, 4:]
    class_ids = class_scores.argmax(axis=1)
    confs = class_scores.max(axis=1)
    keep = confs >= CONFIDENCE_THRESHOLD
    pred, class_ids, confs = pred[keep], class_ids[keep], confs[keep]

    detections = []
    if len(pred):
        boxes = []
        for cx, cy, w, h in pred[:, :4]:
            x1 = max(0, int((cx - w / 2 - pad_x) / scale))
            y1 = max(0, int((cy - h / 2 - pad_y) / scale))
            x2 = min(orig_w, int((cx + w / 2 - pad_x) / scale))
            y2 = min(orig_h, int((cy + h / 2 - pad_y) / scale))
            boxes.append([x1, y1, x2 - x1, y2 - y1])

        idxs = cv2.dnn.NMSBoxes(boxes, confs.tolist(), CONFIDENCE_THRESHOLD, NMS_THRESHOLD)
        for i in np.array(idxs).flatten():
            x, y, w, h = boxes[i]
            label = CLASS_NAMES.get(int(class_ids[i]), f"class_{int(class_ids[i])}")
            conf = float(confs[i])
            detections.append((label, conf))
            cv2.rectangle(img, (x, y), (x + w, y + h), (0, 0, 255), 3)
            cv2.putText(
                img, f"{label} {conf:.2f}", (x, max(y - 10, 15)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2,
            )

    cv2.imwrite(str(save_path), img)
    return detections


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
# DETECTION HELPERS
# =====================================================================
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
    "structural": "Potential Structural Damage",
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


def classify_detections(detections):
    """detections = [(label, conf), ...] -> (damage summary, best confidence, all detected labels)"""
    if not detections:
        return "No Confirmed Damage Detected", 0.0, []

    detected_labels = [f"{label} {conf:.0%}" for label, conf in detections]
    damage_candidates = []

    for label, conf in detections:
        mapped = DAMAGE_MAP.get(normalize_label(label))
        if mapped:
            damage_candidates.append((conf, mapped))

    if not damage_candidates:
        return "Object Detected - Damage Not Confirmed", 0.0, detected_labels

    damage_candidates.sort(key=lambda item: item[0], reverse=True)
    best_confidence, best_damage = damage_candidates[0]

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
8. Set is_infrastructure to true ONLY if the main subject of the photo is school infrastructure, a building part, or a facility/fixture/furniture item (wall, ceiling, floor, roof, door, window, stairs, railing, pipe, wiring, desk, chair, etc.). Set it to false if the main subject is a person, face, animal, plant, food, vehicle, sky/scenery, or any other non-facility subject.

Return ONLY valid JSON with exactly these keys:
{{
  "image_description": "A concise description of what is visibly present in the image.",
  "damage_assessment": "Explain whether damage is confirmed by YOLO and what the image suggests.",
  "risk_level": "Low, Medium, or High",
  "immediate_actions": ["Action 1", "Action 2"],
  "avoid_actions": ["Thing to avoid 1"],
  "maintenance_priority": "A short priority statement and who should inspect it.",
  "visual_observations": ["Visible observation 1", "Visible observation 2"],
  "is_infrastructure": true
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
                "maxOutputTokens": 4096,
            },
        }

        model_name = getattr(settings, "GEMINI_MODEL", "gemini-3.6-flash").strip()
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

        raw_infra = analysis.get("is_infrastructure", True)
        if isinstance(raw_infra, bool):
            is_infrastructure = raw_infra
        else:
            is_infrastructure = str(raw_infra).strip().lower() not in {"false", "no", "0"}

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
            "is_infrastructure": is_infrastructure,
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
    "vehicle", "bike", "motorcycle", "bicycle", "food", "drink",
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


@require_POST
def submit_report(request, report_id):
    """Tinatawag ng Submit Report button sa damage_check.html (via fetch)."""
    report = get_object_or_404(DetectionResult, id=report_id)

    if not _can_submit(report):
        return JsonResponse(
            {"ok": False, "error": "Only infrastructure/facility damage reports can be submitted."},
            status=400,
        )

    if report.submitted:
        return JsonResponse({"ok": True, "already": True, "report_id": report.pk})

    try:
        report.submitted = True
        report.save(update_fields=["submitted"])
    except Exception:
        logger.exception("Could not mark report %s as submitted", report.pk)
        return JsonResponse(
            {"ok": False, "error": "Could not save the report. Check the Django terminal for details."},
            status=500,
        )

    return JsonResponse({"ok": True, "already": False, "report_id": report.pk})


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
                image_path = Path(report.image.path)
                if not image_path.is_file():
                    raise FileNotFoundError(f"Uploaded image not found: {image_path}")

                if get_model() is None:
                    report.damage_type = "Detection Unavailable - Model Not Loaded"
                    report.confidence = 0.0
                    report.save()
                    error_message = (
                        "The ONNX model could not be loaded. Place best.onnx in the project "
                        "root or detection/ folder, then restart Django."
                    )
                else:
                    detected_rel_path = f"detections/detected/{report.pk}_detected.jpg"
                    detected_abs_path = Path(settings.MEDIA_ROOT) / detected_rel_path
                    detected_abs_path.parent.mkdir(parents=True, exist_ok=True)

                    detections = run_onnx_detection(image_path, detected_abs_path)
                    damage_found, confidence, detected_labels = classify_detections(detections)

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
            "can_submit_report": _can_submit(latest) and (
                not gemini_analysis
                or not gemini_analysis.get("available")
                or gemini_analysis.get("is_infrastructure", True)
            ),
            "location": location,
        },
    )


# =====================================================================
# DASHBOARD (mga na-submit na report lang)
# =====================================================================
def dashboard(request):
    location = request.GET.get("location") or getattr(settings, "OPENWEATHER_CITY", "Manito,PH")
    weather, weather_error = get_current_weather(location)

    reports = DetectionResult.objects.filter(submitted=True).order_by("-created_at")

    # Subukan ang mga pangalan ng template na ito, ayon sa pagkakasunod.
    # Kung iba ang pangalan ng dashboard file mo, idagdag dito.
    return render(request, ["detection/dashboard.html", "detection/upload.html"], {
        "reports": reports,
        "latest": reports.first(),
        "weather": weather,
        "weather_error": weather_error,
        "location": location,
    })


# =====================================================================
# REPORTS
# =====================================================================
def reports_list(request):
    location = request.GET.get("location") or getattr(settings, "OPENWEATHER_CITY", "Manito,PH")
    all_reports = DetectionResult.objects.all().order_by("-created_at")

    reports_data = []
    for r in all_reports:
        damage = r.damage_type or ""
        if damage.startswith("Detection"):
            status_class, status_text = "failed", "Analysis Failed"
        elif damage in NO_DAMAGE_LABELS:
            status_class, status_text = "pending", "No Damage Found"
        elif getattr(r, "submitted", False):
            status_class, status_text = "submitted", "Report Submitted"
        else:
            status_class, status_text = "pending", "Not Submitted"

        reports_data.append(
            {
                "id": r.id,
                "damage_type": damage or "Unspecified Damage",
                "location": getattr(r, "location", None) or location,
                "created_at": r.created_at,
                "confidence": (r.confidence or 0) * 100,
                "image": r.image,
                "status_class": status_class,
                "status_text": status_text,
            }
        )

    return render(request, "detection/reports.html", {"reports": reports_data})


def _report_status(damage, submitted=False):
    damage = damage or ""
    if damage.startswith("Detection"):
        return "failed", "Analysis Failed"
    if damage in NO_DAMAGE_LABELS:
        return "pending", "No Damage Found"
    if submitted:
        return "submitted", "Report Submitted"
    return "pending", "Not Submitted"


def report_detail(request, report_id):
    location = request.GET.get("location") or getattr(settings, "OPENWEATHER_CITY", "Manito,PH")
    report = get_object_or_404(DetectionResult, id=report_id)

    status_class, status_text = _report_status(
        report.damage_type, getattr(report, "submitted", False)
    )

    return render(
        request,
        "detection/report_detail.html",
        {
            "report": report,
            "status_class": status_class,
            "status_text": status_text,
            "confidence_pct": (report.confidence or 0) * 100,
            "location": getattr(report, "location", None) or location,
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


from django.contrib.auth.decorators import login_required


#@login_required
def settings_view(request):
    """Display and handle user/system settings for the teacher dashboard"""

    # Current settings values - can later be saved to User profile
    context = {
        "page_title": "Settings",
        "settings": {
            "dashboard_refresh_rate": "5",  # minutes
            "default_risk_view": "all",
            "notifications_enabled": True,
            "email_alerts": True,
            "low_confidence_visibility": False,
            "items_per_page": "10",
            "timezone": "Asia/Manila",
        }
    }

    if request.method == "POST":
        # Get values from form
        dashboard_refresh = request.POST.get("dashboard_refresh_rate", "5")
        default_view = request.POST.get("default_risk_view", "all")
        notifications = request.POST.get("notifications_enabled") == "on"
        email_alerts = request.POST.get("email_alerts") == "on"
        show_low_confidence = request.POST.get("low_confidence_visibility") == "on"
        items_per_page = request.POST.get("items_per_page", "10")
        timezone = request.POST.get("timezone", "Asia/Manila")

        # Save to session for now (later you can store in User profile)
        request.session["dashboard_refresh_rate"] = dashboard_refresh
        request.session["default_risk_view"] = default_view
        request.session["notifications_enabled"] = notifications
        request.session["email_alerts"] = email_alerts
        request.session["low_confidence_visibility"] = show_low_confidence
        request.session["items_per_page"] = items_per_page
        request.session["timezone"] = timezone

        messages.success(request, "Settings saved successfully! ✅")
        return redirect("settings")

    return render(request, "detection/settings.html", context)


def logout_view(request):
    logout(request)
    return redirect("detection:login")