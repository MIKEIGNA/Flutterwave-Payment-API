from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

status_choices = (
    ("pending", "Pending"),
    ("paid", "Paid"),
    ("successful", "Successfully Paid"),
    ("cancelled", "Cancelled"),
)

class Payment(models.Model):
    name = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    payment_method = models.CharField(max_length=255, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="UGX")
    project = models.CharField(max_length=255, null=True, blank=True, default="")
    status = models.CharField(max_length=20, choices=status_choices, default="pending")
    transaction_id = models.CharField(max_length=255, unique=True)
    flutterwave_response = models.JSONField(blank=True, null=True)  # For storing the response
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment {self.transaction_id} - {self.status}"
