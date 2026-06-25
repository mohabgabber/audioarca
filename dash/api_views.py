from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class ThemePreferenceSerializer(serializers.Serializer):
    theme = serializers.ChoiceField(choices=("light", "dark"))


class UserThemePreferenceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        return Response({"theme": request.user.theme_preference})

    def patch(self, request, *args, **kwargs):
        serializer = ThemePreferenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        request.user.theme_preference = serializer.validated_data["theme"]
        request.user.save(update_fields=["theme_preference"])
        return Response({"theme": request.user.theme_preference}, status=status.HTTP_200_OK)
