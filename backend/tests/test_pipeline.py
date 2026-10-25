def test_customer_detail_form_is_read_in_order():
    from app.services.pipeline import parse_detail_form

    parsed = parse_detail_form(
        "Name: Aisha Rahman\nPhone: +971501112233\nCompany: Marina Interiors\n"
        "Location: Dubai Marina\nRequirements: Motorized blinds\nBudget: 2000-4000 AED\nDeadline: 15-11-2026\n"
        "Name: Quoted duplicate\n"
    )
    assert parsed["name"] == "Aisha Rahman"
    assert parsed["phone"] == "+971501112233"
    assert parsed["budget"] == "2000-4000 AED"
    assert "name" not in parse_detail_form("Name: \nPhone: -")


def test_processing_a_sales_email_links_a_real_lead(client, auth_header):
    from app.database import SessionLocal
    from app.gmail.parser import parse_gmail_message
    from app.gmail.sync import upsert_parsed_message
    from app.services.pipeline import process_stored_email
    from tests.test_gmail_and_worker import _gmail_payload

    db = SessionLocal()
    try:
        parsed = parse_gmail_message(
            _gmail_payload(
                message_id="enquiry-1",
                subject="Quotation for motorized blinds",
                body="Hello, I need motorized blinds for a villa in Dubai Marina. Budget is 8000 AED. Please quote.",
            )
        )
        email, created = upsert_parsed_message(db, parsed)
        db.commit()
        email_id = email.id
        assert created is True
        result = process_stored_email(db, email_id, use_llm=False)
        db.commit()
    finally:
        db.close()

    assert result["link_result"] in {"created", "updated_existing"}
    assert result["lead_id"]
    lead = client.get(f"/api/v1/leads/{result['lead_id']}", headers=auth_header)
    assert lead.status_code == 200
    body = lead.json()
    assert body["source"] == "gmail"
    assert body["is_synthetic"] is False
    assert body["email"] == "aisha.rahman.example@example.com"
    assert "8000" in (body["requirements"] or "") or body["budget_min"] == 8000
    emails = client.get(f"/api/v1/leads/{result['lead_id']}/emails", headers=auth_header)
    assert emails.json()["items"][0]["id"] == email_id

    again = process_stored_email(SessionLocal(), email_id, use_llm=False)
    assert again["status"] == "already_processed"


def test_overview_uses_database_counts(client, auth_header):
    client.post(
        "/api/v1/leads",
        headers=auth_header,
        json={"name": "Hot One", "priority": "hot", "status": "quotation", "next_follow_up_at": "2020-01-01T00:00:00"},
    )
    overview = client.get("/api/v1/overview", headers=auth_header)
    assert overview.status_code == 200
    body = overview.json()
    assert body["total_leads"] >= 1
    assert body["hot"] >= 1
    assert body["leads_by_status"]["quotation"] >= 1
    assert body["overdue_follow_ups"] >= 1
