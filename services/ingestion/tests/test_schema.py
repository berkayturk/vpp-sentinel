from ingestion.schema import ParseError, Telemetry, parse_telemetry

GOOD = (
    b'{"device_id":"como-charger-001","site":"como","device_type":"charger",'
    b'"ts":"2026-10-31T14:05:00Z","firmware":"2.3.1","status":"charging",'
    b'"power_kw":7.4,"energy_kwh":12.5,"soc_pct":null,"voltage_v":231.2}'
)

# Shape sent by charger firmware 2.4.0 (incident VppSiteDevicesDark, como, 2026-11-01):
# "ts" is an integer count of milliseconds since the Unix epoch.
FW_240_EPOCH_MS = (
    b'{"device_id":"como-charger-002","site":"como","device_type":"charger",'
    b'"ts":1793540889104,"firmware":"2.4.0","status":"charging",'
    b'"power_kw":22.0,"energy_kwh":1.406,"soc_pct":null,"voltage_v":228.3}'
)


def test_parses_normal_payload():
    t = parse_telemetry(GOOD)
    assert isinstance(t, Telemetry)
    assert t.ts.tzinfo is not None and t.ts.utcoffset().total_seconds() == 0
    assert t.soc_pct is None
    assert t.power_kw == 7.4


def test_parses_offset_timestamp_to_utc():
    t = parse_telemetry(GOOD.replace(b"14:05:00Z", b"16:05:00+02:00"))
    assert isinstance(t, Telemetry)
    assert t.ts.isoformat() == "2026-10-31T14:05:00+00:00"


def test_parses_firmware_240_epoch_millisecond_timestamp():
    t = parse_telemetry(FW_240_EPOCH_MS)
    assert isinstance(t, Telemetry), t
    assert t.ts.isoformat() == "2026-11-01T13:48:09.104000+00:00"
    assert t.firmware == "2.4.0"
    assert t.power_kw == 22.0


def test_epoch_seconds_integer_is_rejected():
    # Ten-digit epoch seconds would silently land in 1970 if read as milliseconds.
    garbled = FW_240_EPOCH_MS.replace(b'"ts":1793540889104', b'"ts":1793540889')
    assert parse_telemetry(garbled) == ParseError("invalid_timestamp", "charger", "2.4.0")


def test_negative_epoch_timestamp_is_rejected():
    garbled = FW_240_EPOCH_MS.replace(b'"ts":1793540889104', b'"ts":-1')
    assert parse_telemetry(garbled) == ParseError("invalid_timestamp", "charger", "2.4.0")


def test_absurdly_large_epoch_timestamp_is_rejected():
    garbled = FW_240_EPOCH_MS.replace(b'"ts":1793540889104', b'"ts":99999999999999999999')
    assert parse_telemetry(garbled) == ParseError("invalid_timestamp", "charger", "2.4.0")


def test_float_epoch_timestamp_is_rejected():
    garbled = FW_240_EPOCH_MS.replace(b'"ts":1793540889104', b'"ts":1793540889104.5')
    assert parse_telemetry(garbled) == ParseError("invalid_timestamp", "charger", "2.4.0")


def test_boolean_timestamp_is_rejected():
    garbled = FW_240_EPOCH_MS.replace(b'"ts":1793540889104', b'"ts":true')
    assert parse_telemetry(garbled) == ParseError("invalid_timestamp", "charger", "2.4.0")


def test_invalid_json_is_reported_not_raised():
    assert parse_telemetry(b"{not json") == ParseError("invalid_json", None, None)


def test_non_object_json_is_invalid():
    assert parse_telemetry(b"[1, 2, 3]") == ParseError("invalid_json", None, None)


def test_missing_field():
    e = parse_telemetry(GOOD.replace(b'"voltage_v":231.2', b'"x":1'))
    assert isinstance(e, ParseError)
    assert e.reason == "missing_field"
    assert e.device_type == "charger" and e.firmware == "2.3.1"


def test_unparseable_timestamp_is_rejected():
    garbled = GOOD.replace(b'"ts":"2026-10-31T14:05:00Z"', b'"ts":"yesterday"')
    assert parse_telemetry(garbled) == ParseError("invalid_timestamp", "charger", "2.3.1")


def test_negative_energy_rejected():
    e = parse_telemetry(GOOD.replace(b'"energy_kwh":12.5', b'"energy_kwh":-1'))
    assert isinstance(e, ParseError) and e.reason == "invalid_value"


def test_soc_out_of_range_rejected():
    e = parse_telemetry(GOOD.replace(b'"soc_pct":null', b'"soc_pct":140'))
    assert isinstance(e, ParseError) and e.reason == "invalid_value"


def test_unknown_device_type_rejected():
    e = parse_telemetry(GOOD.replace(b'"device_type":"charger"', b'"device_type":"toaster"'))
    assert isinstance(e, ParseError) and e.reason == "invalid_value"


def test_telemetry_is_immutable():
    t = parse_telemetry(GOOD)
    assert isinstance(t, Telemetry)
    try:
        t.power_kw = 1.0  # type: ignore[misc]
    except (TypeError, ValueError):
        return
    raise AssertionError("Telemetry must be frozen")
