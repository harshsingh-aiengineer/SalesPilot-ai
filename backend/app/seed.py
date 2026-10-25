"""Seed fictional Dubai blinds leads. Does not create Gmail messages.

Usage:
    python -m app.seed --yes
    python -m app.seed --reset --yes
"""

from __future__ import annotations

import argparse
from datetime import timedelta

from app.bootstrap import init_app
from app.database import SessionLocal
from app.models import Lead, LeadActivity, LeadNote
from app.schemas import LeadCreate
from app.services.leads import create_lead
from app.timeutils import utcnow

LEADS = [
    {
        "name": "Aisha Rahman",
        "email": "aisha.rahman.example@example.com",
        "phone": "+971-50-000-1101",
        "company": "Rahman Residence",
        "location": "Dubai Marina",
        "requirements": "Motorized blackout blinds for 3 bedrooms. Width and height not measured yet.",
        "budget_min": 8000,
        "budget_max": 12000,
        "status": "qualified",
        "priority": "hot",
        "qualification_score": 78,
        "qualification_summary": "Synthetic seed. Budget stated, measurements missing, follow-up overdue.",
        "missing_information": '["measurements"]',
        "next_action": "Request window measurements and book installation survey.",
        "follow_up_days": -3,
        "notes": "SYNTHETIC SEED. Fictional Dubai Marina enquiry.",
    },
    {
        "name": "Omar Al Farsi",
        "email": "omar.alfarsi.example@example.com",
        "phone": "+971-50-000-1102",
        "company": None,
        "location": "Jumeirah",
        "requirements": "Blackout blinds for a nursery. Needs child-safe cordless option.",
        "budget_min": None,
        "budget_max": None,
        "status": "new",
        "priority": "warm",
        "qualification_score": 52,
        "qualification_summary": "Synthetic seed. Product interest is clear. Budget unknown.",
        "missing_information": '["budget", "measurements"]',
        "next_action": "Send a clarification email for budget and measurements.",
        "follow_up_days": 1,
        "notes": "SYNTHETIC SEED.",
    },
    {
        "name": "Northwind Offices",
        "email": "facilities.northwind.example@example.com",
        "phone": "+971-4-000-2200",
        "company": "Northwind Offices LLC",
        "location": "Business Bay",
        "requirements": "Office roller blinds for 18 windows. Quotation requested for next month fit-out.",
        "budget_min": 25000,
        "budget_max": 40000,
        "status": "quotation",
        "priority": "hot",
        "qualification_score": 84,
        "qualification_summary": "Synthetic seed. Waiting for a quotation.",
        "missing_information": "[]",
        "next_action": "Prepare the office blinds quotation.",
        "follow_up_days": 0,
        "notes": "SYNTHETIC SEED. Waiting for quotation.",
    },
    {
        "name": "Lena Hoffmann",
        "email": "lena.hoffmann.example@example.com",
        "phone": None,
        "company": None,
        "location": "Arabian Ranches",
        "requirements": "Just browsing curtain colours. No installation date.",
        "budget_min": None,
        "budget_max": None,
        "status": "new",
        "priority": "cold",
        "qualification_score": 28,
        "qualification_summary": "Synthetic seed. Early research, low urgency.",
        "missing_information": '["budget", "measurements", "deadline"]',
        "next_action": "Keep as a nurture lead.",
        "follow_up_days": 14,
        "notes": "SYNTHETIC SEED.",
    },
    {
        "name": "Khalid Noor",
        "email": "khalid.noor.example@example.com",
        "phone": "+971-55-000-1105",
        "company": None,
        "location": "Deira",
        "requirements": "Installation enquiry for existing curtain tracks. Tracks are already on site.",
        "budget_min": None,
        "budget_max": None,
        "status": "contacted",
        "priority": "warm",
        "qualification_score": 48,
        "qualification_summary": "Synthetic seed. Installation only. Budget missing.",
        "missing_information": '["budget"]',
        "next_action": "Confirm site access and labour budget.",
        "follow_up_days": -1,
        "notes": "SYNTHETIC SEED. Overdue follow-up.",
    },
    {
        "name": "Priya Menon",
        "email": "priya.menon.example@example.com",
        "phone": "+971-52-000-1106",
        "company": None,
        "location": "Dubai Marina",
        "requirements": "Motorized blinds, similar to another Marina enquiry. Review before merging.",
        "budget_min": 7000,
        "budget_max": 9000,
        "status": "new",
        "priority": "warm",
        "qualification_score": 60,
        "qualification_summary": "Synthetic seed. Possible duplicate-like enquiry. Different contact, do not auto-merge.",
        "missing_information": '["measurements"]',
        "next_action": "Review against Aisha Rahman before creating a quotation.",
        "follow_up_days": 2,
        "notes": "SYNTHETIC SEED. Duplicate-like enquiry requiring review. Not the same person as Aisha Rahman.",
    },
    {
        "name": "Studio Lumen",
        "email": "hello.studiolumen.example@example.com",
        "phone": "+971-4-000-3300",
        "company": "Studio Lumen",
        "location": "Al Quoz",
        "requirements": "Curtains and sheer panels for a studio showroom.",
        "budget_min": None,
        "budget_max": None,
        "status": "qualified",
        "priority": "warm",
        "qualification_score": 55,
        "qualification_summary": "Synthetic seed. Missing measurements and deadline.",
        "missing_information": '["measurements", "budget", "deadline"]',
        "next_action": "Ask for showroom measurements.",
        "follow_up_days": 3,
        "notes": "SYNTHETIC SEED.",
    },
    {
        "name": "Hassan Iqbal",
        "email": "hassan.iqbal.example@example.com",
        "phone": "+971-50-000-1108",
        "company": None,
        "location": "Downtown Dubai",
        "requirements": "Blackout blinds for a living room. Customer asked for a call last week.",
        "budget_min": 4000,
        "budget_max": 6000,
        "status": "contacted",
        "priority": "hot",
        "qualification_score": 74,
        "qualification_summary": "Synthetic seed. Overdue follow-up after a requested call.",
        "missing_information": '["measurements"]',
        "next_action": "Call about the missed follow-up.",
        "follow_up_days": -6,
        "notes": "SYNTHETIC SEED. Overdue.",
    },
    {
        "name": "Maya Chen",
        "email": "maya.chen.example@example.com",
        "phone": None,
        "company": None,
        "location": "Palm Jumeirah",
        "requirements": "Package of curtains and motorized blinds for a villa.",
        "budget_min": 15000,
        "budget_max": 22000,
        "status": "new",
        "priority": "hot",
        "qualification_score": 70,
        "qualification_summary": "Synthetic seed. No phone number on file.",
        "missing_information": '["phone", "measurements"]',
        "next_action": "Email Maya for a phone number and measurements.",
        "follow_up_days": 1,
        "notes": "SYNTHETIC SEED.",
    },
    {
        "name": "Desert Pearl Hotel",
        "email": "procurement.desertpearl.example@example.com",
        "phone": "+971-4-000-4400",
        "company": "Desert Pearl Hotel",
        "location": "Al Barsha",
        "requirements": "Bulk office and guest-room blinds. Quotation requested for 40 rooms.",
        "budget_min": 60000,
        "budget_max": 90000,
        "status": "quotation",
        "priority": "hot",
        "qualification_score": 88,
        "qualification_summary": "Synthetic seed. Fictional hotel procurement. Waiting for quotation.",
        "missing_information": "[]",
        "next_action": "Send the room-count quotation.",
        "follow_up_days": 0,
        "notes": "SYNTHETIC SEED. Fictional hotel. Not a real property.",
    },
]


def reset_synthetic(db) -> int:
    leads = db.query(Lead).filter(Lead.is_synthetic.is_(True), Lead.source == "synthetic_seed").all()
    count = len(leads)
    for lead in leads:
        db.delete(lead)
    db.commit()
    return count


def seed(db) -> int:
    created = 0
    for item in LEADS:
        exists = db.query(Lead).filter(Lead.email == item["email"], Lead.is_synthetic.is_(True)).one_or_none()
        if exists is not None:
            continue
        follow_days = item["follow_up_days"]
        lead = create_lead(
            db,
            LeadCreate(
                name=item["name"],
                email=item["email"],
                phone=item["phone"],
                company=item["company"],
                location=item["location"],
                requirements=item["requirements"],
                budget_min=item["budget_min"],
                budget_max=item["budget_max"],
                currency="AED",
                source="synthetic_seed",
                status=item["status"],
                priority=item["priority"],
                next_action=item["next_action"],
                next_follow_up_at=utcnow() + timedelta(days=follow_days),
                notes=item["notes"],
                is_synthetic=True,
            ),
            actor="seed",
        )
        lead.qualification_score = item["qualification_score"]
        lead.qualification_summary = item["qualification_summary"]
        lead.missing_information = item["missing_information"]
        lead.qualification_method = "seed"
        lead.qualification_confidence = 1.0
        db.add(LeadNote(lead_id=lead.id, body="SYNTHETIC SEED record. Not a real customer.", author_type="seed"))
        db.add(
            LeadActivity(
                lead_id=lead.id,
                activity_type="seed",
                summary="Marked as synthetic development data.",
            )
        )
        created += 1
    db.commit()
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic SalesPilot leads")
    parser.add_argument("--reset", action="store_true", help="Delete previously seeded synthetic leads first")
    parser.add_argument("--yes", action="store_true", help="Confirm that you want to write synthetic leads")
    args = parser.parse_args()
    if not args.yes:
        raise SystemExit("Refusing to seed without --yes. This writes fictional leads marked synthetic_seed.")
    init_app()
    db = SessionLocal()
    try:
        removed = reset_synthetic(db) if args.reset else 0
        created = seed(db)
    finally:
        db.close()
    print(f"removed_synthetic={removed} created={created}")


if __name__ == "__main__":
    main()
