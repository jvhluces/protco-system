from django.db import models

class DamageReport(models.Model):
    image = models.ImageField(upload_to='damage_reports/original/')
    detected_image = models.ImageField(upload_to='damage_reports/detected/', blank=True, null=True)
    damage_type = models.CharField(max_length=200, default="Pending Analysis")
    confidence = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]  # Newest first automatically

    def __str__(self):
        display = self.damage_type or "Report"
        return f"{display} — {self.created_at.strftime('%Y-%m-%d %H:%M')}"