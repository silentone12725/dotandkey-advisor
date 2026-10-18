from scripts.enrich_product_graph import extract_html_sections, extract_faqs_from_html


def test_extract_html_sections_and_faqs_from_product_html():
    html = """
    <html><body>
    <h2>Ingredients</h2>
    <p>Niacinamide, Hyaluronic Acid, Ceramides.</p>
    <h2>Benefits</h2>
    <p>Brightening and hydration for dry skin.</p>
    <h2>FAQ</h2>
    <p>Can I use this daily? Ans: Yes, it is suitable for daily use.</p>
    </body></html>
    """

    sections = extract_html_sections(html)
    assert "ingredients" in sections
    assert "Niacinamide" in sections["ingredients"]
    assert "Brightening" in sections["benefits"]
    assert "page_context" in sections

    faqs = extract_faqs_from_html(html)
    assert len(faqs) >= 1
    assert "daily" in faqs[0]["question"].lower()
