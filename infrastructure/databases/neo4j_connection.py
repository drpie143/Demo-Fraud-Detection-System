from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Neo4jDriverResult:
    driver: object
    uri: str
    used_tls_fallback: bool


def _self_signed_uri(uri: str) -> str | None:
    if uri.startswith("neo4j+s://"):
        return uri.replace("neo4j+s://", "neo4j+ssc://", 1)
    if uri.startswith("bolt+s://"):
        return uri.replace("bolt+s://", "bolt+ssc://", 1)
    return None


def open_neo4j_driver(
    *,
    uri: str,
    user: str,
    password: str,
    allow_self_signed_fallback: bool = True,
) -> Neo4jDriverResult:
    """Open a Neo4j driver and optionally retry with +ssc for local TLS issues."""

    from neo4j import GraphDatabase

    candidates = [uri]
    fallback_uri = _self_signed_uri(uri)
    if allow_self_signed_fallback and fallback_uri and fallback_uri != uri:
        candidates.append(fallback_uri)

    last_exc: Exception | None = None
    for index, candidate_uri in enumerate(candidates):
        driver = GraphDatabase.driver(candidate_uri, auth=(user, password))
        try:
            driver.verify_connectivity()
            return Neo4jDriverResult(
                driver=driver,
                uri=candidate_uri,
                used_tls_fallback=index > 0,
            )
        except Exception as exc:
            try:
                driver.close()
            except Exception:
                pass

            # Auth errors are real credential problems; trying a different TLS
            # trust mode would only add noise.
            if type(exc).__name__ == "AuthError":
                raise

            last_exc = exc

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Neo4j connection failed without an exception")
