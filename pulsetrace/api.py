from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        if response.status_code >= status.HTTP_500_INTERNAL_SERVER_ERROR:
            response.data = {"detail": "Internal server error."}
        return response

    return Response(
        {"detail": "Internal server error."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
