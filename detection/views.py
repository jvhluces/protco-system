import logging
from pathlib import Path

from django.conf import settings
from django.shortcuts import render

from ultralytics import YOLO

from .models import DamageReport


logger = logging.getLogger(__name__)


# =========================================================
# 1. YOLO MODEL CONFIGURATION
# =========================================================
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
    except Exception as e:
        logger.exception("Failed to load YOLO model from %s: %s", MODEL_PATH, str(e))
else:
    logger.error(
        "YOLO model not found. Place best.pt beside manage.py "
        "or inside the detection/ folder. Checked paths:\n%s",
        "\n".join(f"- {p}" for p in MODEL_CANDIDATES)
    )


# =========================================================
# 2. DETECTION SETTINGS
# =========================================================
CONFIDENCE_THRESHOLD = 0.15
IMAGE_SIZE = 1024


# =========================================================
# 3. DAMAGE CLASS MAPPING
# =========================================================
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
    """Normalize a model class label for consistent matching."""
    return " ".join(str(label).strip().lower().replace("_", " ").split())


def classify_detections(result):
    """
    Interpret YOLO detections — only known damage classes count as damage.
    Returns: (summary_text, highest_confidence, list_of_detected_labels)
    """
    detected_labels = []
    damage_candidates = []
    max_damage_confidence = 0.0

    if not result.boxes or len(result.boxes) == 0:
        return ("No Confirmed Damage Detected", 0.0, detected_labels)

    for box in result.boxes:
        cls_id = int(box.cls[0].item())
        confidence = float(box.conf[0].item())
        raw_label = str(result.names.get(cls_id, f"class_{cls_id}"))
        normalized_label = normalize_label(raw_label)

        detected_labels.append(f"{raw_label} {confidence:.0%}")

        mapped_damage = DAMAGE_MAP.get(normalized_label)
        if mapped_damage:
            damage_candidates.append((confidence, mapped_damage, raw_label))
            if confidence > max_damage_confidence:
                max_damage_confidence = confidence

    if damage_candidates:
        damage_candidates.sort(key=lambda x: x[0], reverse=True)
        best_confidence, best_damage, _ = damage_candidates[0]

        if len(damage_candidates) > 1:
            damage_found = (
                f"{best_damage} "
                f"(Additional Potential Damage: "
                f"{len(damage_candidates) - 1} more detection(s))"
            )
        else:
            damage_found = best_damage

        return (damage_found, best_confidence, detected_labels)

    return ("Object Detected - Damage Not Confirmed", 0.0, detected_labels)


# =========================================================
# 4. IMAGE UPLOAD AND DETECTION VIEW
# =========================================================
def upload_and_detect(request):
    latest = None
    error_message = None
    template_name = (
        "detection/damage_check.html"
        if request.resolver_match and request.resolver_match.url_name == "damage_check"
        else "detection/upload.html"
    )

    if request.method == "POST" and request.FILES.get("image"):
        img_file = request.FILES["image"]

        # Validate file type
        allowed_exts = {".jpg", ".jpeg", ".png", ".webp"}
        ext = Path(img_file.name).suffix.lower()
        if ext not in allowed_exts:
            error_message = (
                f"Unsupported file type. Allowed: JPG, PNG, WebP. "
                f"Got: {ext}"
            )
            reports = DamageReport.objects.all().order_by("-created_at")
            return render(
                request,
                template_name,
                {"reports": reports, "latest": latest, "error_message": error_message},
            )

        # Create report record — matches YOUR models.py exactly
        report = DamageReport.objects.create(image=img_file)

        try:
            if model is None:
                report.damage_type = "Detection Unavailable — Model Not Loaded"
                report.confidence = 0.0
                report.save()
                error_message = (
                    "The YOLO model could not be loaded. Place best.pt in the "
                    "project root or detection/ folder, then restart the server."
                )
            else:
                image_path = Path(report.image.path)
                if not image_path.exists():
                    raise FileNotFoundError(f"Uploaded image missing: {image_path}")

                # Run inference
                results = model.predict(
                    source=str(image_path),
                    conf=CONFIDENCE_THRESHOLD,
                    imgsz=IMAGE_SIZE,
                    verbose=False,
                )
                result = results[0]

                # Save annotated image — matches YOUR upload_to path
                detected_rel_path = f"damage_reports/detected/{report.pk}_detected.jpg"
                detected_abs_path = Path(settings.MEDIA_ROOT) / detected_rel_path
                detected_abs_path.parent.mkdir(parents=True, exist_ok=True)
                result.save(filename=str(detected_abs_path))

                # Analyze results
                damage_found, confidence, detected_labels = classify_detections(result)

                report.damage_type = damage_found
                report.confidence = confidence
                report.detected_image = detected_rel_path
                report.save()

                logger.info(
                    "Detection complete | ID:%s | %s | conf:%.3f | labels:%s",
                    report.pk, damage_found, confidence, detected_labels
                )

        except Exception as exc:
            logger.exception(f"Detection failed for report ID {report.pk}: {exc}")
            report.damage_type = "Detection Failed — Check Server Logs"
            report.confidence = 0.0
            report.save()
            error_message = (
                f"Image analysis failed: {str(exc)[:100]}. "
                "See Django terminal logs for full details."
            )

        latest = report

    reports = DamageReport.objects.all().order_by("-created_at")
    return render(
        request,
        template_name,
        {
            "reports": reports,
            "latest": latest,
            "error_message": error_message,
        },
    )