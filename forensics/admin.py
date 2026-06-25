from django.contrib import admin

from forensics.models import (
    AcousticFeatureSet,
    AnalysisJob,
    AnalysisResult,
    Case,
    EvidenceArtifact,
    EventLog,
    LinguisticFeatureSet,
    Report,
    ReportVersion,
    TextSample,
    UploadedAudioSample,
)


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ("case_number", "name", "case_type", "status", "reviewer_status", "progress_percentage")
    list_filter = ("case_type", "status", "reviewer_status", "adverse_condition_flag")
    search_fields = ("case_number", "name")


admin.site.register(UploadedAudioSample)
admin.site.register(TextSample)
admin.site.register(EvidenceArtifact)
admin.site.register(AnalysisJob)
admin.site.register(AnalysisResult)
admin.site.register(AcousticFeatureSet)
admin.site.register(LinguisticFeatureSet)
admin.site.register(Report)
admin.site.register(ReportVersion)
admin.site.register(EventLog)
