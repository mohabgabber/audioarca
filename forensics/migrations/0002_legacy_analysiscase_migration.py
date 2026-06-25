from django.db import migrations


def migrate_legacy_analysis_cases(apps, schema_editor):
    AnalysisCase = apps.get_model("dash", "AnalysisCase")
    UserModel = apps.get_model("dash", "UserModel")
    Case = apps.get_model("forensics", "Case")
    ModelVersion = apps.get_model("forensics", "ModelVersion")

    created_by = UserModel.objects.order_by("date_joined").first()
    if created_by:
        for legacy_case in AnalysisCase.objects.all().order_by("created_at", "id"):
            if Case.objects.filter(legacy_analysis_case_id=legacy_case.id).exists():
                continue
            prefix = "LEG-PHN" if legacy_case.analysis_type == "phonetic" else "LEG-LNG"
            case_number = f"{prefix}-{legacy_case.id:05d}"
            Case.objects.create(
                case_number=case_number,
                name=legacy_case.name,
                description=legacy_case.description,
                case_type=legacy_case.analysis_type,
                status="draft",
                reviewer_status="not_reviewed",
                progress_percentage=0,
                current_stage="upload",
                created_by=created_by,
                legacy_analysis_case_id=legacy_case.id,
                created_at=legacy_case.created_at,
                updated_at=legacy_case.updated_at,
            )

    defaults = [
        ("phonetic", "speechbrain-ecapa-speaker-compare", "v1", "speechbrain/spkrec-ecapa-voxceleb"),
        ("linguistic", "local-stylometry", "v1", "scikit-learn-local"),
        ("transcription", "faster-whisper", "small", "faster-whisper"),
        ("language_id", "fasttext-lid", "lid.176", "model_assets/fasttext/lid.176.bin"),
        ("reporting", "openai-reporting", "gpt-5.4", "openai"),
    ]
    for domain, name, version, source in defaults:
        ModelVersion.objects.get_or_create(
            domain=domain,
            name=name,
            version=version,
            defaults={
                "source": source,
                "validation_status": "missing",
                "metadata": {"seeded": True},
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("forensics", "0001_initial"),
        ("dash", "0005_usermodel_role_invitation"),
    ]

    operations = [
        migrations.RunPython(migrate_legacy_analysis_cases, migrations.RunPython.noop),
    ]
