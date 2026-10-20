"""Count provider calls after local preflight; SDK retries are not separate calls here."""
from contextlib import contextmanager
from contextvars import ContextVar

_usage = ContextVar('llm_usage', default=None)


@contextmanager
def measure_llm_usage():
    stats = {'requests': 0, 'input_tokens': 0, 'output_tokens': 0, 'token_reports': 0, 'providers': {}}
    token = _usage.set(stats)
    try:
        yield stats
    finally:
        _usage.reset(token)


def record_request(provider):
    stats = _usage.get()
    if stats is not None:
        stats['requests'] += 1
        stats['providers'][provider] = stats['providers'].get(provider, 0) + 1


def record_tokens(input_tokens, output_tokens):
    stats = _usage.get()
    if stats is not None and isinstance(input_tokens, int) and isinstance(output_tokens, int):
        stats['input_tokens'] += input_tokens
        stats['output_tokens'] += output_tokens
        stats['token_reports'] += 1
