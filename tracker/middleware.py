from django.conf import settings

class CorsMiddleware:
    # Read-only daa service: allow a separate map front to fetch the JSON data.
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resp = self.get_response(request)
        if request.path.startswith("/api/"):
            resp["Access-Control-Allow-Origin"] = settings.CORS_ALLOW_ORIGIN
        return resp