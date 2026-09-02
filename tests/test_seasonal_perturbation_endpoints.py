"""
Tests for /api/admin/seasonal-perturbations endpoints (iter #37).

Covers:
- GET  /api/admin/seasonal-perturbations
- POST /api/admin/seasonal-perturbations
- DELETE /api/admin/seasonal-perturbations/{id}
- POST /api/admin/seasonal-perturbations/{id}/deactivate
- Integration: /api/seasonal-factors now exposes active perturbations
"""

import pytest


@pytest.fixture
def client():
    """FastAPI TestClient with startup triggered (persistence init).

    Cleans the seasonal_perturbations table before AND after each test
    to avoid polluting the shared data/simulation.db (used by HF).
    """
    from fastapi.testclient import TestClient
    from web.backend import main as backend_main
    import sqlite3

    def _clear_table():
        conn = sqlite3.connect("data/simulation.db")
        conn.execute("DELETE FROM seasonal_perturbations")
        conn.commit()
        conn.close()

    _clear_table()
    with TestClient(backend_main.app) as c:
        yield c
    _clear_table()


# ============================================
# GET — list perturbations
# ============================================

class TestListPerturbations:
    def test_empty_list(self, client):
        """No perturbations yet → empty list, count=0."""
        resp = client.get("/api/admin/seasonal-perturbations")
        assert resp.status_code == 200
        data = resp.json()
        assert "perturbations" in data
        assert "count" in data
        assert data["count"] == 0
        assert data["perturbations"] == []

    def test_filter_by_sim_day(self, client):
        """?sim_day=N returns only perturbations active at N."""
        client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "early",
                "start_sim_day": 0,
                "end_sim_day": 5,
                "material_type": "concrete",
                "multiplier": 1.5,
            },
        )
        client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "late",
                "start_sim_day": 50,
                "end_sim_day": 60,
                "material_type": "metal_scrap",
                "multiplier": 0.8,
            },
        )
        # sim_day=3 → only early
        resp = client.get("/api/admin/seasonal-perturbations?sim_day=3")
        assert resp.status_code == 200
        data = resp.json()
        labels = [p["label"] for p in data["perturbations"]]
        assert "early" in labels
        assert "late" not in labels

    def test_filter_by_material_type(self, client):
        client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "concrete-only",
                "start_sim_day": 0,
                "end_sim_day": 100,
                "material_type": "concrete",
                "multiplier": 1.5,
            },
        )
        client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "wildcard",
                "start_sim_day": 0,
                "end_sim_day": 100,
                "material_type": "*",
                "multiplier": 1.2,
            },
        )
        resp = client.get(
            "/api/admin/seasonal-perturbations?sim_day=50&material_type=concrete"
        )
        labels = [p["label"] for p in resp.json()["perturbations"]]
        assert set(labels) == {"concrete-only", "wildcard"}


# ============================================
# POST — add perturbation
# ============================================

class TestAddPerturbation:
    def test_valid_creates_row(self, client):
        resp = client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "Holiday surge",
                "start_sim_day": 350,
                "end_sim_day": 365,
                "material_type": "paper_cardboard",
                "multiplier": 1.5,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "created" in data
        created = data["created"]
        assert created["label"] == "Holiday surge"
        assert created["multiplier"] == 1.5
        assert created["active"] is True
        assert created["id"] is not None

    def test_invalid_multiplier_returns_400(self, client):
        """multiplier > MAX_MULTIPLIER (3.0) should reject."""
        resp = client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "extreme",
                "start_sim_day": 0,
                "end_sim_day": 5,
                "material_type": "concrete",
                "multiplier": 10.0,
            },
        )
        assert resp.status_code == 400
        assert "multiplier" in resp.json()["detail"].lower()

    def test_end_before_start_returns_400(self, client):
        resp = client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "bad-window",
                "start_sim_day": 30,
                "end_sim_day": 10,
                "material_type": "*",
                "multiplier": 1.0,
            },
        )
        assert resp.status_code == 400

    def test_empty_label_returns_400(self, client):
        resp = client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "",
                "start_sim_day": 0,
                "end_sim_day": 5,
                "material_type": "concrete",
                "multiplier": 1.0,
            },
        )
        assert resp.status_code == 400

    def test_wildcard_material_type_accepted(self, client):
        resp = client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "global-cut",
                "start_sim_day": 0,
                "end_sim_day": 5,
                "material_type": "*",
                "multiplier": 0.5,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["created"]["material_type"] == "*"


# ============================================
# DELETE — remove perturbation
# ============================================

class TestDeletePerturbation:
    def test_delete_existing_returns_200(self, client):
        create = client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "to-delete",
                "start_sim_day": 0,
                "end_sim_day": 5,
                "material_type": "concrete",
                "multiplier": 1.0,
            },
        )
        pid = create.json()["created"]["id"]
        resp = client.delete(f"/api/admin/seasonal-perturbations/{pid}")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/admin/seasonal-perturbations/99999")
        assert resp.status_code == 404

    def test_after_delete_no_longer_active(self, client):
        create = client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "to-delete",
                "start_sim_day": 0,
                "end_sim_day": 100,
                "material_type": "concrete",
                "multiplier": 1.0,
            },
        )
        pid = create.json()["created"]["id"]
        client.delete(f"/api/admin/seasonal-perturbations/{pid}")
        # Now list with sim_day=50 → should be empty
        resp = client.get("/api/admin/seasonal-perturbations?sim_day=50")
        assert resp.json()["count"] == 0


# ============================================
# Deactivate — soft-delete
# ============================================

class TestDeactivatePerturbation:
    def test_deactivate_keeps_row_inactive(self, client):
        create = client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "soft-delete",
                "start_sim_day": 0,
                "end_sim_day": 100,
                "material_type": "concrete",
                "multiplier": 1.5,
            },
        )
        pid = create.json()["created"]["id"]
        # Deactivate
        resp = client.post(f"/api/admin/seasonal-perturbations/{pid}/deactivate")
        assert resp.status_code == 200
        # active_only=True (default) should now exclude it
        listing = client.get("/api/admin/seasonal-perturbations")
        assert listing.json()["count"] == 0
        # active_only=False should still see it (audit-friendly)
        all_listing = client.get(
            "/api/admin/seasonal-perturbations?active_only=false"
        )
        assert all_listing.json()["count"] == 1
        # And it's marked active=0
        row = all_listing.json()["perturbations"][0]
        assert row["active"] == 0


# ============================================
# Integration: /api/seasonal-factors exposes perturbations
# ============================================

class TestSeasonalFactorsIntegration:
    def test_seasonal_factors_returns_active_perturbations(self, client):
        """When perturbations active at sim_day=X, /api/seasonal-factors
        returns them in the response (no auth needed for read)."""
        # Create a perturbation covering sim_day=15
        client.post(
            "/api/admin/seasonal-perturbations",
            params={
                "label": "weather-cut",
                "start_sim_day": 10,
                "end_sim_day": 20,
                "material_type": "*",
                "multiplier": 0.7,
            },
        )
        resp = client.get("/api/seasonal-factors?sim_day=15")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_perturbations" in data
        labels = [p["label"] for p in data["active_perturbations"]]
        assert "weather-cut" in labels

    def test_seasonal_factors_omits_perturbations_field_when_no_sim_day(self, client):
        """When ?sim_day= is omitted, perturbations field is empty list."""
        resp = client.get("/api/seasonal-factors")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("active_perturbations") == []


# ============================================
# Auth: endpoints protected when token set
# ============================================

class TestAuthProtection:
    def test_get_requires_admin_when_token_set(self, monkeypatch):
        """When GL_ADMIN_TOKEN is set, GET endpoint requires auth."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get("/api/admin/seasonal-perturbations")
        assert resp.status_code == 401

    def test_post_requires_admin_when_token_set(self, monkeypatch):
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.post(
                "/api/admin/seasonal-perturbations",
                params={
                    "label": "x",
                    "start_sim_day": 0,
                    "end_sim_day": 1,
                    "material_type": "concrete",
                    "multiplier": 1.0,
                },
            )
        assert resp.status_code == 401

    def test_authenticated_request_works(self, monkeypatch):
        """With correct token, request succeeds."""
        monkeypatch.setenv("GL_ADMIN_TOKEN", "secret-abc")
        from fastapi.testclient import TestClient
        from web.backend import main as backend_main
        with TestClient(backend_main.app) as client:
            resp = client.get(
                "/api/admin/seasonal-perturbations",
                headers={"X-Admin-Token": "secret-abc"},
            )
        assert resp.status_code == 200
