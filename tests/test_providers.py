from app import providers


def _fake(slug, name, modes, state=None):
    return {"slug": slug, "name": name, "modes": modes,
            "locations": [{"city": "Indianapolis", "state": state}] if state else [{"country": "US"}],
            "relationship": {"type": "partner", "disclosure": f"Aster partners with {name}."},
            "contact": {}, "booking": "contact", "services": [{"need": "s_dietitian", "label": "Dietitian"}]}


def test_catalog_files_are_valid_and_have_no_checkout_links():
    assert set(providers.catalog()) == {"essence", "helix_harvest"}
    for p in providers.catalog().values():
        assert p["relationship"]["disclosure"].startswith("Aster partners with")
        assert all(s["need"] in providers.NEEDS for s in p["services"]), p["slug"]
        assert "checkout" not in str(p).lower(), p["slug"]  # decision D11: contact details only
    assert set(providers.neutral_options()) <= set(providers.NEEDS)


def test_existing_relationship_only_breaks_ties(monkeypatch):
    monkeypatch.setattr(providers, "catalog", lambda: {
        "zeta": _fake("zeta", "Zeta Clinic", ["virtual"]),
        "alpha": _fake("alpha", "Alpha Nutrition", ["virtual"]),
    })
    names = lambda r: [o["name"] for o in r["options"]]  # noqa: E731
    assert names(providers.match("s_dietitian")) == ["Alpha Nutrition", "Zeta Clinic"]  # no default preference
    linked = providers.match("s_dietitian", linked={"zeta"})
    assert names(linked) == ["Zeta Clinic", "Alpha Nutrition"]
    assert "already a client" in linked["options"][0]["reasons"][-1]


def test_a_better_fit_beats_an_existing_relationship(monkeypatch):
    monkeypatch.setattr(providers, "catalog", lambda: {
        "local": _fake("local", "Local Clinic", ["in_person"], state="IN"),
        "online": _fake("online", "Online Nutrition", ["virtual"]),
    })
    r = providers.match("s_dietitian", mode="virtual", linked={"local"})
    assert [o["name"] for o in r["options"]] == ["Online Nutrition"]
    assert r["excluded"] == [{"provider": "local", "why": "no virtual option"}]
    # State unknown: the in-person clinic scores lower and says so, even for its own client
    r = providers.match("s_dietitian", linked={"local"})
    assert [o["name"] for o in r["options"]] == ["Online Nutrition", "Local Clinic"]
    assert "check that works for you" in r["options"][1]["uncertainties"][0]


def test_location_age_and_caution_filters_on_the_real_catalog():
    r = providers.match("s_medical_weight", state="OH")
    assert r["options"] == [] and r["excluded"] == [{"provider": "essence", "why": "not in your area"}]
    assert "primary care" in r["neutral"]
    r = providers.match("s_medical_weight", state="IN")
    assert r["options"][0]["name"] == "Essence Aesthetics & Wellness"
    assert "Starts with a consultation" in r["options"][0]["uncertainties"][0]
    assert providers.match("s_dietitian", age=16)["options"] == []
    assert providers.match("s_medical_weight", state="IN", caution=True)["options"] == []
    assert providers.match("s_dietitian", caution=True)["options"]  # a dietitian is fine with a nutrition concern


def test_limited_evidence_note_and_disclosure_travel_with_the_option():
    o = providers.match("s_dna_test")["options"][0]
    assert "evidence" in o["uncertainties"][0] and o["disclosure"]


def test_unknown_need_and_banned_terms():
    assert providers.match("s_spaceflight")["error"] == "unknown_need"
    assert {"Essence", "HelixVit", "Helix & Harvest"} <= set(providers.banned_plan_terms())
