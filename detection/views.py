
import logging
from pathlib import Path

from django.conf import settings
from django.shortcuts import render

from ultralytics import YOLO

from .models import DamageReport


logger = logging.getLogger(__name__)




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
    """Normalize a label before matching it to a damage category."""
    return " ".join(
        str(label).strip().lower().replace("_", " ").split()
    )


def get_damage_category(damage_name):
    """
    Group report descriptions into broad categories.
    This is for report comparison, not structural risk assessment.
    """
    name = normalize_label(damage_name)

    categories = {
        "crack": [
            "crack",
            "fracture",
            "fissure",
        ],
        "window": [
            "window",
            "broken glass",
            "glass",
        ],
        "electrical": [
            "electrical",
            "wiring",
            "wire",
            "outlet",
            "exposed cable",
        ],
        "water": [
            "water leak",
            "leak",
            "water damage",
            "dampness",
            "mold",
        ],
        "ceiling": [
            "ceiling",
            "roof",
            "collapse",
        ],
        "floor": [
            "floor",
            "stairs",
            "walkway",
            "tile",
        ],
        "furniture": [
            "chair",
            "desk",
            "table",
            "bench",
            "furniture",
        ],
        "wall": [
            "wall damage",
            "hole in wall",
        ],
        "door": [
            "door",
        ],
    }

    for category, keywords in categories.items():
        if any(keyword in name for keyword in keywords):
            return category

    return "general"




def classify_detections(result):
    """
    Return:
        damage summary,
        highest confidence among recognized damage detections,
        all detected labels.
    """
    detected_labels = []
    damage_candidates = []

    boxes = result.boxes

    if boxes is None or len(boxes) == 0:
        return (
            "No Confirmed Damage Detected",
            0.0,
            detected_labels,
        )

    for box in boxes:
        cls_id = int(box.cls[0].item())
        confidence = float(box.conf[0].item())

        names = result.names
        if isinstance(names, dict):
            raw_label = str(names.get(cls_id, f"class_{cls_id}"))
        else:
            raw_label = str(names[cls_id])

        normalized_label = normalize_label(raw_label)

        detected_labels.append(
            f"{raw_label} {confidence:.0%}"
        )

        mapped_damage = DAMAGE_MAP.get(normalized_label)

        if mapped_damage:
            damage_candidates.append(
                (confidence, mapped_damage, raw_label)
            )

    if not damage_candidates:
        if detected_labels:
            return (
                "Object Detected - Damage Not Confirmed",
                0.0,
                detected_labels,
            )

        return (
            "No Confirmed Damage Detected",
            0.0,
            detected_labels,
        )

    damage_candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    best_confidence, best_damage, _ = damage_candidates[0]

    if len(damage_candidates) > 1:
        damage_found = (
            f"{best_damage} "
            f"(Additional Potential Damage: "
            f"{len(damage_candidates) - 1} more detection(s))"
        )
    else:
        damage_found = best_damage

    return (
        damage_found,
        best_confidence,
        detected_labels,
    )



def count_previous_similar_reports(latest, reports):
    """
    Count older reports in the same broad damage category.
    Does not prove that the reports refer to the same physical location.
    """
    if latest is None:
        return 0

    current_category = get_damage_category(latest.damage_type)

    if current_category == "general":
        return 0

    count = 0

    for previous in reports:
        if previous.pk == latest.pk:
            continue

        previous_category = get_damage_category(
            previous.damage_type
        )

        if previous_category == current_category:
            count += 1

    return count


def upload_and_detect(request):
    latest = None
    error_message = None
    previous_similar_count = 0


    reports = DamageReport.objects.all().order_by("-created_at")


    resolver_match = getattr(request, "resolver_match", None)

    template_name = (
        "detection/damage_check.html"
        if resolver_match
        and resolver_match.url_name == "damage_check"
        else "detection/upload.html"
    )

 

    if request.method == "POST" and request.FILES.get("image"):
        img_file = request.FILES["image"]
        extension = Path(img_file.name).suffix.lower()

        if extension not in ALLOWED_EXTENSIONS:
            error_message = (
                "Unsupported file type. "
                "Please upload a JPG, JPEG, PNG, or WebP image."
            )

        else:
            report = DamageReport.objects.create(
                image=img_file
            )

            try:
                if model is None:
                    report.damage_type = (
                        "Detection Unavailable - Model Not Loaded"
                    )
                    report.confidence = 0.0
                    report.save()

                    error_message = (
                        "The YOLO model could not be loaded. "
                        "Place best.pt in the project root or "
                        "detection/ folder, then restart Django."
                    )

                else:
                    image_path = Path(report.image.path)

                    if not image_path.is_file():
                        raise FileNotFoundError(
                            f"Uploaded image not found: {image_path}"
                        )

               
                    results = model.predict(
                        source=str(image_path),
                        conf=CONFIDENCE_THRESHOLD,
                        imgsz=IMAGE_SIZE,
                        verbose=False,
                    )

                    result = results[0]

 
                    detected_rel_path = (
                        f"damage_reports/detected/"
                        f"{report.pk}_detected.jpg"
                    )

                    detected_abs_path = (
                        Path(settings.MEDIA_ROOT)
                        / detected_rel_path
                    )

                    detected_abs_path.parent.mkdir(
                        parents=True,
                        exist_ok=True,
                    )

                    result.save(
                        filename=str(detected_abs_path)
                    )

                 
                    (
                        damage_found,
                        confidence,
                        detected_labels,
                    ) = classify_detections(result)

                    report.damage_type = damage_found
                    report.confidence = confidence
                    report.detected_image = detected_rel_path
                    report.save()

                    logger.info(
                        "Detection complete | ID:%s | "
                        "damage:%s | confidence:%.3f | labels:%s",
                        report.pk,
                        damage_found,
                        confidence,
                        detected_labels,
                    )

            except Exception as exc:
                logger.exception(
                    "Detection failed for report ID %s",
                    report.pk,
                )

                report.damage_type = (
                    "Detection Failed - Check Server Logs"
                )
                report.confidence = 0.0
                report.save()

                error_message = (
                    "Image analysis failed. "
                    "Check the Django terminal for details."
                )

            latest = report

 

    reports = DamageReport.objects.all().order_by("-created_at")

    # Count previous reports with the same damage category.
    previous_similar_count = count_previous_similar_reports(
        latest,
        reports,
    )

 
    return render(
        request,
        template_name,
        {
            "reports": reports,
            "latest": latest,
            "error_message": error_message,
            "previous_similar_count": previous_similar_count,
        },
    )
def weather_forecast(request):
    return render(request, 'detection/weather_forecast.html')