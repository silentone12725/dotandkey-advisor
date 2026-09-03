from scripts.browser_scrape import extract_faq_pairs, extract_product_text, load_product_handles


HTML = """
<html>
  <body>
    <main>
      <h1>Glow Serum</h1>
      <p>Brightening serum for dry skin.</p>
      <section class="faq-item">
        <h3>Can I use it daily?</h3>
        <p>Yes, it is safe for daily use.</p>
      </section>
      <section class="faq-item">
        <h3>Is it fragrance free?</h3>
        <p>Yes, it is fragrance free and gentle.</p>
      </section>
    </main>
  </body>
</html>
"""


def test_extract_product_text_uses_main_content():
    body = extract_product_text(HTML)
    assert "Glow Serum" in body
    assert "Brightening serum for dry skin" in body


def test_extract_faq_pairs_handles_questions_and_answers():
    faqs = extract_faq_pairs(HTML)
    assert len(faqs) == 2
    assert faqs[0]["question"] == "Can I use it daily?"
    assert "daily use" in faqs[0]["answer"].lower()


def test_load_product_handles_uses_product_media_json():
    media = {
        "SKU_1": {"handle": "product-one"},
        "SKU_2": {"handle": ""},
        "SKU_3": {"handle": "product-three"},
    }
    handles = load_product_handles(media)
    assert handles == ["product-one", "product-three"]
