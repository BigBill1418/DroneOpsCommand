"""ADR-0050 schema surface: customers.source_ref, missions.lead_writeback_at."""
from datetime import datetime

from app.models.customer import Customer
from app.models.mission import Mission
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.schemas.mission import MissionResponse


def test_models_have_columns():
    assert "source_ref" in Customer.__table__.columns
    assert "lead_writeback_at" in Mission.__table__.columns
    assert Customer.__table__.columns["source_ref"].nullable
    assert Mission.__table__.columns["lead_writeback_at"].nullable


def test_customer_schemas_accept_source_ref():
    assert CustomerCreate(name="A", source_ref="web-1").source_ref == "web-1"
    assert CustomerUpdate(source_ref="cold-2").source_ref == "cold-2"


def test_mission_response_exposes_lead_writeback_at():
    assert "lead_writeback_at" in MissionResponse.model_fields
    assert MissionResponse.model_fields["lead_writeback_at"].default is None


def test_migration_0013_chains_from_0012():
    import importlib.util, pathlib
    p = pathlib.Path(__file__).parents[1] / "alembic/versions/0013_lead_integration.py"
    spec = importlib.util.spec_from_file_location("m0013", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.down_revision == "0012_cf_access_ident"
    assert m.revision == "0013_lead_integration"
