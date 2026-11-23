"""
Professional PDF E-Ticket & Boarding Pass Generator using ReportLab.
Produces print-ready, high-resolution PDF boarding passes with embedded QR codes.
"""
import io
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Image as PlatypusImage,
)
from .crypto import generate_qr_code_pil_image


def generate_ticket_pdf(ticket) -> io.BytesIO:
    """
    Builds an audit-ready, beautifully formatted PDF e-ticket for the given Ticket model instance.
    Returns an in-memory BytesIO buffer containing the PDF document.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()

    # Custom typography styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#0f172a'),
        alignment=0
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=12,
        textColor=colors.HexColor('#64748b')
    )
    section_heading = ParagraphStyle(
        'SectionHeading',
        parent=styles['Heading3'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=14,
        textColor=colors.HexColor('#1e293b'),
        spaceAfter=6
    )
    label_style = ParagraphStyle(
        'FieldLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#64748b')
    )
    value_style = ParagraphStyle(
        'FieldValue',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=13,
        textColor=colors.HexColor('#0f172a')
    )
    seat_badge_style = ParagraphStyle(
        'SeatBadge',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=18,
        leading=20,
        textColor=colors.HexColor('#4338ca'),
        alignment=1
    )
    terms_style = ParagraphStyle(
        'TermsText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=7,
        leading=9,
        textColor=colors.HexColor('#94a3b8')
    )

    story = []

    # 1. Header Banner Table
    booking = ticket.booking
    trip = ticket.trip
    seat = ticket.seat
    seat_record = ticket.booking_seat

    header_data = [
        [
            Paragraph("BUS TICKETING SYSTEM", title_style),
            Paragraph(f"<b>PNR:</b> {booking.pnr}<br/><b>Ticket:</b> {ticket.ticket_number}", ParagraphStyle(
                'HeaderRight', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=14, alignment=2, textColor=colors.HexColor('#0f172a')
            ))
        ],
        [
            Paragraph("Official E-Ticket & Verified Boarding Pass", subtitle_style),
            Paragraph(f"<b>Issued:</b> {ticket.created_at.strftime('%Y-%m-%d %H:%M')}", subtitle_style)
        ]
    ]
    header_table = Table(header_data, colWidths=[3.8 * inch, 3.8 * inch])
    header_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LINEBELOW', (0, 1), (-1, 1), 1.5, colors.HexColor('#4338ca')),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 14))

    # 2. Main Content Split Table (Left: Details, Right: QR Code)
    # Generate QR Code image in memory
    qr_pil = generate_qr_code_pil_image(ticket.qr_token)
    qr_buffer = io.BytesIO()
    qr_pil.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)
    qr_image = PlatypusImage(qr_buffer, width=2.1 * inch, height=2.1 * inch)

    dep_time_str = trip.departure_datetime.strftime("%d %b %Y, %I:%M %p")
    from_term = seat_record.from_stop.terminal
    to_term = seat_record.to_stop.terminal

    journey_table_data = [
        [Paragraph("PASSENGER NAME", label_style), Paragraph("SEAT NUMBER", label_style)],
        [Paragraph(f"<b>{ticket.passenger_name}</b> ({seat_record.gender})", value_style), Paragraph(f"<b>{ticket.seat_number}</b>", seat_badge_style)],
        [Paragraph("DEPARTURE TIME", label_style), Paragraph("BUS & CLASS", label_style)],
        [Paragraph(dep_time_str, value_style), Paragraph(f"{trip.bus.registration_number} &bull; {trip.bus.get_bus_type_display()}", value_style)],
        [Paragraph("ORIGIN / BOARDING TERMINAL", label_style), Paragraph("DESTINATION / ALIGHTING", label_style)],
        [Paragraph(f"<b>{from_term.city}</b> ({from_term.name})", value_style), Paragraph(f"<b>{to_term.city}</b> ({to_term.name})", value_style)],
        [Paragraph("FARE & TAXES", label_style), Paragraph("TICKET STATUS", label_style)],
        [Paragraph(f"PKR {seat_record.price} (Tax & Fee Included)", value_style), Paragraph(f"<b>{ticket.status}</b>", value_style)],
    ]
    journey_table = Table(journey_table_data, colWidths=[2.6 * inch, 2.6 * inch])
    journey_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#f1f5f9')),
        ('BACKGROUND', (1, 1), (1, 1), colors.HexColor('#e0e7ff')),
    ]))

    qr_box_data = [
        [qr_image],
        [Paragraph("<b>SCAN AT BOARDING GATE</b>", ParagraphStyle('QRTitle', fontName='Helvetica-Bold', fontSize=8, alignment=1, textColor=colors.HexColor('#1e293b')))],
        [Paragraph("Cryptographically signed with tamper verification", ParagraphStyle('QRSub', fontName='Helvetica', fontSize=7, alignment=1, textColor=colors.HexColor('#64748b')))],
    ]
    qr_table = Table(qr_box_data, colWidths=[2.2 * inch])
    qr_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#ffffff')),
    ]))

    main_split_table = Table([[journey_table, qr_table]], colWidths=[5.3 * inch, 2.3 * inch])
    main_split_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (1, 0), (1, 0), 10),
    ]))
    story.append(main_split_table)
    story.append(Spacer(1, 14))

    # 3. Security Terms & Conditions Footer
    terms_text = (
        "<b>IMPORTANT PASSENGER INSTRUCTIONS & TERMS OF CARRIAGE:</b><br/>"
        "1. Passengers must present this official digital or printed Boarding Pass alongside an original CNIC or Government Photo ID.<br/>"
        "2. Boarding gate opens 30 minutes prior to departure and strictly closes 10 minutes before scheduled departure.<br/>"
        "3. Ladies-Only seats are strictly reserved for female passengers. Non-compliance may result in boarding cancellation without refund.<br/>"
        "4. This ticket is cryptographically signed and non-transferable. Duplicate or tampered passes will be rejected at the gate."
    )
    terms_p = Paragraph(terms_text, terms_style)
    terms_table = Table([[terms_p]], colWidths=[7.6 * inch])
    terms_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f1f5f9')),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(terms_table)

    # Build document
    doc.build(story)
    buffer.seek(0)
    return buffer
