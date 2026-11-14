"""Live backend availability and delegation to the unified workflow."""
from compatibility import profiles, runtime


def capabilities():
    try:
        environment = runtime()
    except Exception as exc:
        environment = {"compatible": False, "reason": str(exc)}
    return {"live_player_game_code_collection": environment["compatible"],
            "supported_live_player_backends": ["windows-readonly-profile"] if environment["compatible"] else [],
            "computer_use_confirmation_required": True, "environment": environment,
            "known_profiles": [{k: p[k] for k in ("id", "os", "architecture", "player_version", "player_sha256")} for p in profiles()],
            "offline_import_formats": ["Roblox XML/binary", "rcr-dump/v1", "USSI", "Luau/Roblox bytecode", "source trees"],
            "official_asset_delivery": True, "runtime_injection": "not_implemented"}


def player_acquire(target, output, backend="auto", **options):
    from workflow import run_live
    return run_live(target, output, **options)
