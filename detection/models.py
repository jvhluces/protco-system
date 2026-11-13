from django.db import models
from django.utils import timezone

  

class DetectionResult(models.Model):
    submitted = models.BooleanField(default=False)
    image = models.ImageField(upload_to="detections/original/")
    
    detected_image = models.ImageField(
        upload_to="detections/detected/",
        blank=True,
        null=True,
    )
    damage_type = models.CharField(max_length=255, blank=True, null=True)
    confidence = models.FloatField(blank=True, null=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Detection {self.id} - {self.damage_type or 'Pending'}"