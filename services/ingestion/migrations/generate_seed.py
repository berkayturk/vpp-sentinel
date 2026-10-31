"""Print the seed SQL for sites and devices so it matches the simulator's fleet.

The output is pasted into ``0001_schema.sql``; the SQL stays static and
reviewable, and this script is the single source of truth if the fleet changes.

Run from the repository root:
    uv run --directory services/fleet-simulator python ../ingestion/migrations/generate_seed.py
"""

from fleet_simulator.fleet import DEFAULT_FLEET, build_fleet

SITE_REGIONS = {
    "lugano-south": "Ticino",
    "lugano-north": "Ticino",
    "bellinzona": "Ticino",
    "como": "Lombardy",
    "varese": "Lombardy",
}


def main() -> None:
    print("INSERT INTO sites (id, region) VALUES")
    print(",\n".join(f"  ('{site}', '{SITE_REGIONS[site]}')" for site in DEFAULT_FLEET.sites))
    print("ON CONFLICT (id) DO NOTHING;")
    print()
    print("INSERT INTO devices (id, site_id, device_type, firmware) VALUES")
    rows = [
        f"  ('{d.id}', '{d.site}', '{d.device_type}', '{d.firmware}')"
        for d in build_fleet(DEFAULT_FLEET)
    ]
    print(",\n".join(rows))
    print("ON CONFLICT (id) DO NOTHING;")


if __name__ == "__main__":
    main()
