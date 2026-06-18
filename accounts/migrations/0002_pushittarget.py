import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PushItTarget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=64)),
                ("app_token", models.CharField(max_length=255)),
                ("base_url", models.CharField(default="https://pushit-api.foxugly.com/api/v1", max_length=255)),
                ("title", models.CharField(default="FoxRunner", max_length=128)),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "owner",
                    models.ForeignKey(
                        db_column="owner_user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pushit_targets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "pushit_targets",
                "ordering": ["name"],
            },
        ),
        migrations.AddConstraint(
            model_name="pushittarget",
            constraint=models.UniqueConstraint(fields=["owner", "name"], name="uq_pushit_target_owner_name"),
        ),
    ]
