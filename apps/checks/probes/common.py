from time import perf_counter


class ProbeError(Exception):
    retryable = True


class ProbeExecutionError(ProbeError):
    retryable = True


class ProbeValidationError(ProbeError):
    retryable = False


def elapsed_ms(start_time):
    return round((perf_counter() - start_time) * 1000.0, 3)
