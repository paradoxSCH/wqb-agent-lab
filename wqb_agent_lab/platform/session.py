"""Repository-owned requests session for the WorldQuant BRAIN API."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Generator, Iterable, Mapping, Sized
from typing import Any
from urllib.parse import quote, urlencode, urljoin

import requests
from requests import Response

from wqb_agent_lab.runtime import OperationJournal, SideEffectUncertainError, classify_transport_exception


WQB_API_URL = "https://api.worldquantbrain.com"
URL_AUTHENTICATION = f"{WQB_API_URL}/authentication"
URL_OPERATORS = f"{WQB_API_URL}/operators"
URL_DATASETS = f"{WQB_API_URL}/data-sets"
URL_DATASETS_DATASETID = f"{URL_DATASETS}/{{}}"
URL_DATAFIELDS = f"{WQB_API_URL}/data-fields"
URL_DATAFIELDS_FIELDID = f"{URL_DATAFIELDS}/{{}}"
URL_USERS_SELF_ALPHAS = f"{WQB_API_URL}/users/self/alphas"
URL_ALPHAS_ALPHAID = f"{WQB_API_URL}/alphas/{{}}"
URL_ALPHAS_ALPHAID_CHECK = f"{URL_ALPHAS_ALPHAID}/check"
URL_ALPHAS_ALPHAID_SUBMIT = f"{URL_ALPHAS_ALPHAID}/submit"
URL_ALPHAS_ALPHAID_PNL = f"{URL_ALPHAS_ALPHAID}/recordsets/pnl"
URL_SIMULATIONS = f"{WQB_API_URL}/simulations"
LOCATION = "Location"
RETRY_AFTER = "Retry-After"
_REQUEST_OPTION_NAMES = frozenset(
    {
        "allow_redirects",
        "auth",
        "cert",
        "cookies",
        "headers",
        "hooks",
        "proxies",
        "stream",
        "timeout",
        "verify",
    }
)


class WQBAuthenticationError(RuntimeError):
    """Raised when a WQB session cannot authenticate."""


class WQBSession(requests.Session):
    """Authenticated WQB HTTP session with the legacy research-session contract.

    The class deliberately contains transport mechanics only. Autonomous simulation and
    submission capability decisions remain at workflow and worker boundaries.
    """

    def __init__(
        self,
        credentials: tuple[str, str],
        *,
        logger: logging.Logger | None = None,
        auth_max_tries: int = 3,
        auth_delay_unexpected: float = 2.0,
        request_timeout_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
        auto_authenticate: bool = True,
        operation_journal: OperationJournal | None = None,
        run_id: str = "",
        auth_lock: threading.Lock | None = None,
    ) -> None:
        super().__init__()
        email, password = credentials
        if not email or not password:
            raise ValueError("WQB email and password are required")
        self.credentials = credentials
        self.logger = logger or logging.getLogger("wqb_agent_lab.platform.session")
        self.auth_max_tries = max(1, int(auth_max_tries))
        self.auth_delay_unexpected = max(0.0, float(auth_delay_unexpected))
        self.request_timeout_seconds = max(1.0, float(request_timeout_seconds))
        self.sleep = sleep
        self.operation_journal = operation_journal
        self.run_id = run_id
        self.auth_lock = auth_lock or threading.Lock()
        self.last_operation_record = None
        self._authenticated = False
        if auto_authenticate:
            self.authenticate()

    @staticmethod
    def expected_location(response: Response) -> bool:
        return response.ok and bool(response.headers.get(LOCATION))

    def authenticate(self) -> Response:
        with self.auth_lock:
            if self._authenticated:
                response = requests.Response()
                response.status_code = 200
                response.url = URL_AUTHENTICATION
                return response
            return self._authenticate_locked()

    def _authenticate_locked(self) -> Response:
        last_response: Response | None = None
        last_error: Exception | None = None
        for attempt in range(1, self.auth_max_tries + 1):
            try:
                last_response = super().request(
                    "POST",
                    URL_AUTHENTICATION,
                    auth=self.credentials,
                    timeout=self.request_timeout_seconds,
                )
                if last_response.status_code in {200, 201}:
                    self._authenticated = True
                    return last_response
                last_error = WQBAuthenticationError(
                    f"WQB authentication failed: HTTP {last_response.status_code} "
                    f"{last_response.text[:200]}"
                )
            except requests.RequestException as exc:
                last_error = exc
            if attempt < self.auth_max_tries and self.auth_delay_unexpected > 0:
                self.sleep(self.auth_delay_unexpected)
        raise WQBAuthenticationError(str(last_error or "WQB authentication failed")) from last_error

    def request(
        self,
        method: str,
        url: str,
        *args: Any,
        expected: Callable[[Response], bool] | str | None = None,
        max_tries: int = 1,
        delay_unexpected: float = 2.0,
        **kwargs: Any,
    ) -> Response:
        absolute_url = _absolute_url(url)
        if absolute_url != URL_AUTHENTICATION and not self._authenticated:
            self.authenticate()
        kwargs.setdefault("timeout", self.request_timeout_seconds)
        attempts = max(1, int(max_tries))
        last_response: Response | None = None
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                last_response = super().request(method, absolute_url, *args, **kwargs)
                if last_response.status_code == 401 and absolute_url != URL_AUTHENTICATION:
                    self._authenticated = False
                    if attempt < attempts:
                        self.authenticate()
                        continue
                if _matches_expected(last_response, expected):
                    return last_response
                if not _is_retryable(last_response, expected):
                    return last_response
            except requests.RequestException as exc:
                last_error = exc

            if attempt < attempts:
                delay = _retry_after_seconds(last_response)
                self.sleep(delay if delay is not None else max(0.0, delay_unexpected))

        if last_response is not None:
            return last_response
        assert last_error is not None
        raise last_error

    def get_authentication(self, *args: Any, **kwargs: Any) -> Response:
        return self.get(URL_AUTHENTICATION, *args, **kwargs)

    def head_authentication(self, *args: Any, **kwargs: Any) -> Response:
        return self.head(URL_AUTHENTICATION, *args, **kwargs)

    def delete_authentication(self, *args: Any, **kwargs: Any) -> Response:
        response = self.delete(URL_AUTHENTICATION, *args, **kwargs)
        if response.ok:
            self._authenticated = False
        return response

    def search_operators(self, *args: Any, **kwargs: Any) -> Response:
        kwargs.pop("log", None)
        return self.get(URL_OPERATORS, *args, **kwargs)

    def locate_dataset(self, dataset_id: str, *args: Any, **kwargs: Any) -> Response:
        kwargs.pop("log", None)
        return self.get(URL_DATASETS_DATASETID.format(dataset_id), *args, **kwargs)

    def search_datasets_limited(
        self,
        region: str,
        delay: int,
        universe: str,
        *args: Any,
        instrument_type: str = "EQUITY",
        limit: int = 50,
        offset: int = 0,
        others: Iterable[str] | None = None,
        request_kwargs: Mapping[str, Any] | None = None,
        **filters: Any,
    ) -> Response:
        filters.pop("log", None)
        transport = _extract_request_options(filters, request_kwargs)
        params: list[tuple[str, Any]] = [
            ("region", region),
            ("delay", delay),
            ("universe", universe),
            ("instrumentType", instrument_type),
            ("limit", _bounded(limit, 1, 50)),
            ("offset", _bounded(offset, 0, 10000 - _bounded(limit, 1, 50))),
        ]
        aliases = {
            "search": "search",
            "category": "category",
            "theme": "theme",
            "coverage": "coverage",
            "value_score": "valueScore",
            "alpha_count": "alphaCount",
            "user_count": "userCount",
            "order": "order",
        }
        url = _filtered_url(URL_DATASETS, params, filters, aliases, others)
        return self.get(url, *args, **transport)

    def search_datasets(
        self,
        region: str,
        delay: int,
        universe: str,
        *args: Any,
        limit: int = 50,
        offset: int = 0,
        **kwargs: Any,
    ) -> Generator[Response, None, None]:
        yield from self._pages(
            self.search_datasets_limited,
            region,
            delay,
            universe,
            *args,
            limit=limit,
            offset=offset,
            **kwargs,
        )

    def locate_field(self, field_id: str, *args: Any, **kwargs: Any) -> Response:
        kwargs.pop("log", None)
        return self.get(URL_DATAFIELDS_FIELDID.format(field_id), *args, **kwargs)

    def search_fields_limited(
        self,
        region: str,
        delay: int,
        universe: str,
        *args: Any,
        instrument_type: str = "EQUITY",
        limit: int = 50,
        offset: int = 0,
        others: Iterable[str] | None = None,
        request_kwargs: Mapping[str, Any] | None = None,
        **filters: Any,
    ) -> Response:
        filters.pop("log", None)
        transport = _extract_request_options(filters, request_kwargs)
        page_limit = _bounded(limit, 1, 50)
        params: list[tuple[str, Any]] = [
            ("region", region),
            ("delay", delay),
            ("universe", universe),
            ("instrumentType", instrument_type),
            ("limit", page_limit),
            ("offset", _bounded(offset, 0, 10000 - page_limit)),
        ]
        aliases = {
            "dataset_id": "dataset.id",
            "search": "search",
            "category": "category",
            "theme": "theme",
            "coverage": "coverage",
            "type": "type",
            "alpha_count": "alphaCount",
            "user_count": "userCount",
            "order": "order",
        }
        url = _filtered_url(URL_DATAFIELDS, params, filters, aliases, others)
        return self.get(url, *args, **transport)

    def search_fields(
        self,
        region: str,
        delay: int,
        universe: str,
        *args: Any,
        limit: int = 50,
        offset: int = 0,
        **kwargs: Any,
    ) -> Generator[Response, None, None]:
        yield from self._pages(
            self.search_fields_limited,
            region,
            delay,
            universe,
            *args,
            limit=limit,
            offset=offset,
            **kwargs,
        )

    def locate_alpha(self, alpha_id: str, *args: Any, **kwargs: Any) -> Response:
        kwargs.pop("log", None)
        return self.get(URL_ALPHAS_ALPHAID.format(alpha_id), *args, **kwargs)

    def create_simulation(
        self,
        target: dict[str, Any] | list[Any],
        *args: Any,
        max_tries: int = 12,
        delay_throttled: float = 5.0,
        **kwargs: Any,
    ) -> Response:
        """Create one simulation without replaying ambiguous server responses.

        A 401 or 429 proves that the write was not accepted and is safe to retry. A
        success without ``Location`` and a 5xx response are ambiguous, so callers must
        observe and diagnose them instead of issuing another POST automatically.
        """
        kwargs.pop("log", None)
        kwargs.pop("retry_log", None)
        attempts = max(1, int(max_tries))
        response: Response | None = None
        for attempt in range(1, attempts + 1):
            journal = self.operation_journal
            operation = (
                journal.begin("simulation.create", target, run_id=self.run_id)
                if journal is not None
                else None
            )
            try:
                response = self.request("POST", URL_SIMULATIONS, *args, json=target, max_tries=1, **kwargs)
            except requests.RequestException as exc:
                outcome, reason = classify_transport_exception(exc)
                if operation is not None and journal is not None:
                    self.last_operation_record = journal.finish(
                        operation.operation_id,
                        outcome,
                        reason=reason,
                    )
                if outcome == "not_sent_retryable" and attempt < attempts:
                    self.sleep(max(0.0, delay_throttled))
                    continue
                if outcome == "unknown_commit" and self.last_operation_record is not None:
                    raise SideEffectUncertainError(self.last_operation_record, exc) from exc
                raise

            status_code = int(response.status_code)
            location = str(response.headers.get(LOCATION) or "")
            if status_code in {401, 429}:
                outcome, reason = "not_accepted_retryable", f"http_{status_code}"
            elif 200 <= status_code < 400 and location:
                outcome, reason = "accepted", "location_received"
            elif 200 <= status_code < 400:
                outcome, reason = "unknown_commit", "success_without_location"
            elif status_code >= 500:
                outcome, reason = "unknown_commit", f"server_error_{status_code}"
            else:
                outcome, reason = "rejected", f"http_{status_code}"
            if operation is not None and journal is not None:
                self.last_operation_record = journal.finish(
                    operation.operation_id,
                    outcome,
                    reason=reason,
                    status_code=status_code,
                    remote_ref=location,
                )
            if response.status_code not in {401, 429} or attempt >= attempts:
                return response
            delay = _retry_after_seconds(response)
            self.sleep(delay if delay is not None else max(0.0, delay_throttled))
        assert response is not None
        return response

    def submit_alpha(
        self,
        alpha_id: str,
        *args: Any,
        max_tries: int = 3,
        delay_throttled: float = 5.0,
        **kwargs: Any,
    ) -> Response:
        """Submit one Alpha without replaying an ambiguous POST.

        Authentication and throttle responses prove non-acceptance and may be retried.
        Transport loss and server errors can occur after the platform commits the write,
        so they are journaled for read-only reconciliation instead.
        """
        kwargs.pop("log", None)
        kwargs.pop("retry_log", None)
        attempts = max(1, int(max_tries))
        response: Response | None = None
        payload = {"alpha_id": str(alpha_id)}
        self.last_operation_record = None
        for attempt in range(1, attempts + 1):
            journal = self.operation_journal
            operation = (
                journal.begin("submission.create", payload, run_id=self.run_id)
                if journal is not None
                else None
            )
            try:
                response = self.request(
                    "POST",
                    URL_ALPHAS_ALPHAID_SUBMIT.format(alpha_id),
                    *args,
                    max_tries=1,
                    **kwargs,
                )
            except requests.RequestException as exc:
                outcome, reason = classify_transport_exception(exc)
                if operation is not None and journal is not None:
                    self.last_operation_record = journal.finish(
                        operation.operation_id,
                        outcome,
                        reason=reason,
                    )
                if outcome == "not_sent_retryable" and attempt < attempts:
                    self.sleep(max(0.0, delay_throttled))
                    continue
                if outcome == "unknown_commit" and self.last_operation_record is not None:
                    raise SideEffectUncertainError(self.last_operation_record, exc) from exc
                raise

            status_code = int(response.status_code)
            if status_code in {401, 429}:
                outcome, reason = "not_accepted_retryable", f"http_{status_code}"
            elif 200 <= status_code < 400:
                outcome, reason = "accepted", f"http_{status_code}"
            elif status_code == 408:
                outcome, reason = "unknown_commit", "request_timeout_408"
            elif status_code >= 500:
                outcome, reason = "unknown_commit", f"server_error_{status_code}"
            else:
                outcome, reason = "rejected", f"http_{status_code}"
            if operation is not None and journal is not None:
                self.last_operation_record = journal.finish(
                    operation.operation_id,
                    outcome,
                    reason=reason,
                    status_code=status_code,
                    remote_ref=f"/alphas/{alpha_id}",
                )
            if status_code not in {401, 429} or attempt >= attempts:
                return response
            delay = _retry_after_seconds(response)
            self.sleep(delay if delay is not None else max(0.0, delay_throttled))
        assert response is not None
        return response

    def filter_alphas_limited(
        self,
        *args: Any,
        limit: int = 100,
        offset: int = 0,
        others: Iterable[str] | None = None,
        request_kwargs: Mapping[str, Any] | None = None,
        **filters: Any,
    ) -> Response:
        filters.pop("log", None)
        transport = _extract_request_options(filters, request_kwargs)
        page_limit = _bounded(limit, 1, 100)
        params: list[tuple[str, Any]] = [
            ("limit", page_limit),
            ("offset", _bounded(offset, 0, 10000 - page_limit)),
        ]
        aliases = {
            "competition": "competition",
            "type": "type",
            "language": "settings.language",
            "date_created": "dateCreated",
            "favorite": "favorite",
            "date_submitted": "dateSubmitted",
            "start_date": "os.startDate",
            "status": "status",
            "category": "category",
            "color": "color",
            "tag": "tag",
            "hidden": "hidden",
            "region": "settings.region",
            "instrument_type": "settings.instrumentType",
            "universe": "settings.universe",
            "delay": "settings.delay",
            "decay": "settings.decay",
            "neutralization": "settings.neutralization",
            "truncation": "settings.truncation",
            "unit_handling": "settings.unitHandling",
            "nan_handling": "settings.nanHandling",
            "pasteurization": "settings.pasteurization",
            "sharpe": "is.sharpe",
            "returns": "is.returns",
            "pnl": "is.pnl",
            "turnover": "is.turnover",
            "drawdown": "is.drawdown",
            "margin": "is.margin",
            "fitness": "is.fitness",
            "book_size": "is.bookSize",
            "long_count": "is.longCount",
            "short_count": "is.shortCount",
            "sharpe60": "os.sharpe60",
            "sharpe125": "os.sharpe125",
            "sharpe250": "os.sharpe250",
            "sharpe500": "os.sharpe500",
            "os_is_sharpe_ratio": "os.osISSharpeRatio",
            "pre_close_sharpe": "os.preCloseSharpe",
            "pre_close_sharpe_ratio": "os.preCloseSharpeRatio",
            "self_correlation": "is.selfCorrelation",
            "prod_correlation": "is.prodCorrelation",
            "order": "order",
        }
        fragments: list[str] = list(others or [])
        name = filters.pop("name", None)
        if name is not None:
            name_text = str(name)
            operator = name_text[0] if name_text[:1] in {"~", "="} else "~"
            value = name_text[1:] if name_text[:1] in {"~", "="} else name_text
            fragments.append(f"name{operator}{quote(value, safe='')}")
        url = _filtered_url(URL_USERS_SELF_ALPHAS, params, filters, aliases, fragments)
        return self.get(url, *args, **transport)

    def filter_alphas(
        self,
        *args: Any,
        limit: int = 100,
        offset: int = 0,
        **kwargs: Any,
    ) -> Generator[Response, None, None]:
        yield from self._pages(
            self.filter_alphas_limited,
            *args,
            limit=limit,
            offset=offset,
            **kwargs,
        )

    def _pages(
        self,
        operation: Callable[..., Response],
        *args: Any,
        limit: int,
        offset: int,
        **kwargs: Any,
    ) -> Generator[Response, None, None]:
        count_response = operation(*args, limit=1, offset=offset, **kwargs)
        payload = _json_or_empty(count_response)
        count = int(payload.get("count", 0) or 0) if isinstance(payload, dict) else 0
        for page_offset in range(offset, count, max(1, int(limit))):
            yield operation(*args, limit=limit, offset=page_offset, **kwargs)

    async def simulate(
        self,
        target: dict[str, Any] | list[Any],
        *args: Any,
        max_tries: int | Iterable[Any] = range(600),
        max_create_tries: int = 12,
        **kwargs: Any,
    ) -> Response | None:
        kwargs.pop("log", None)
        kwargs.pop("retry_log", None)
        response = await asyncio.to_thread(self.create_simulation, target, max_tries=max_create_tries)
        location = response.headers.get(LOCATION) if response is not None else None
        if not location:
            return None
        last_response: Response | None = None
        for _ in _attempts(max_tries):
            last_response = await asyncio.to_thread(self.get, location, *args, **kwargs)
            payload = _json_or_empty(last_response)
            if isinstance(payload, dict) and payload.get("alpha"):
                return last_response
            if not last_response.ok:
                return last_response
            status = str(payload.get("status", "")).upper() if isinstance(payload, dict) else ""
            if status in {"ERROR", "FAILED", "CANCELLED", "COMPLETE"}:
                return last_response
            await asyncio.sleep(float(_retry_after_seconds(last_response) or 2.0))
        return last_response

    async def concurrent_simulate(
        self,
        targets: Iterable[dict[str, Any] | list[Any]],
        concurrency: int | asyncio.Semaphore,
        *args: Any,
        return_exceptions: bool = False,
        **kwargs: Any,
    ) -> list[Response | BaseException | None]:
        target_list = targets if isinstance(targets, Sized) else list(targets)
        semaphore = concurrency if isinstance(concurrency, asyncio.Semaphore) else asyncio.Semaphore(concurrency)

        async def run(target: dict[str, Any] | list[Any]) -> Response | None:
            async with semaphore:
                return await self.simulate(target, *args, **kwargs)

        return list(await asyncio.gather(*(run(target) for target in target_list), return_exceptions=return_exceptions))

    async def check(
        self,
        alpha_id: str,
        *args: Any,
        max_tries: int | Iterable[Any] = range(600),
        **kwargs: Any,
    ) -> Response | None:
        kwargs.pop("log", None)
        kwargs.pop("retry_log", None)
        return await self._retry_after("GET", URL_ALPHAS_ALPHAID_CHECK.format(alpha_id), *args, max_tries=max_tries, **kwargs)

    async def submit(
        self,
        alpha_id: str,
        *args: Any,
        max_tries: int | Iterable[Any] = range(600),
        **kwargs: Any,
    ) -> Response | None:
        attempts = len(tuple(_attempts(max_tries)))
        return await asyncio.to_thread(
            self.submit_alpha,
            alpha_id,
            *args,
            max_tries=attempts,
            **kwargs,
        )

    async def _retry_after(
        self,
        method: str,
        url: str,
        *args: Any,
        max_tries: int | Iterable[Any],
        **kwargs: Any,
    ) -> Response | None:
        response: Response | None = None
        for _ in _attempts(max_tries):
            response = self.request(method, url, *args, **kwargs)
            if method.upper() != "GET" and response.status_code not in {401, 429}:
                return response
            retry_after = _retry_after_seconds(response)
            if retry_after is None and response.status_code != 401:
                return response
            await asyncio.sleep(float(retry_after or 0.0))
        return response


def _absolute_url(path_or_url: str) -> str:
    if path_or_url.startswith(("http://", "https://")):
        return path_or_url
    return urljoin(f"{WQB_API_URL}/", path_or_url.lstrip("/"))


def _matches_expected(
    response: Response,
    expected: Callable[[Response], bool] | str | None,
) -> bool:
    if expected is None:
        return True
    if callable(expected):
        return bool(expected(response))
    return bool(response.headers.get(expected))


def _is_retryable(
    response: Response,
    expected: Callable[[Response], bool] | str | None,
) -> bool:
    return response.status_code in {401, 408, 429} or response.status_code >= 500


def _retry_after_seconds(response: Response | None) -> float | None:
    if response is None:
        return None
    value = response.headers.get(RETRY_AFTER) or response.headers.get(RETRY_AFTER.lower())
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def _bounded(value: int, minimum: int, maximum: int) -> int:
    return min(max(int(value), minimum), maximum)


def _filtered_url(
    base_url: str,
    params: list[tuple[str, Any]],
    filters: dict[str, Any],
    aliases: dict[str, str],
    others: Iterable[str] | None,
) -> str:
    fragments: list[str] = []
    for key, api_name in aliases.items():
        value = filters.pop(key, None)
        if value is None:
            continue
        if hasattr(value, "to_params"):
            fragments.extend(str(value.to_params(api_name)).split("&"))
        else:
            params.append((api_name, str(value).lower() if isinstance(value, bool) else value))
    params.extend((key, value) for key, value in filters.items() if value is not None)
    fragments.extend(str(item).lstrip("&?") for item in (others or []))
    query = urlencode(params, doseq=True)
    raw = "&".join(fragment for fragment in fragments if fragment)
    return f"{base_url}?{query}{'&' if query and raw else ''}{raw}"


def _extract_request_options(
    filters: dict[str, Any],
    provided: Mapping[str, Any] | None,
) -> dict[str, Any]:
    options = dict(provided or {})
    for key in _REQUEST_OPTION_NAMES:
        if key in filters:
            value = filters.pop(key)
            options.setdefault(key, value)
    return options


def _json_or_empty(response: Response) -> Any:
    try:
        return response.json()
    except (AttributeError, ValueError):
        return {}


def _attempts(value: int | Iterable[Any]) -> Iterable[Any]:
    return range(max(1, value)) if isinstance(value, int) else value
