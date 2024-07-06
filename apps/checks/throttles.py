from rest_framework.throttling import UserRateThrottle


class RunNowUserThrottle(UserRateThrottle):
    scope = "run_now"
