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


# dotandkey.com marks FAQs with paired class names and puts the question in a
# <p>, not a heading. The original extractor only looked inside h1-h4/strong/
# summary/button, found nothing, and fell through to a regex that captured
# answer prose as "questions". Regression guard for that.
SHOPIFY_ACCORDION_HTML = """
<html><body><div class="accordion__content-inner">
  <ul class="collapsable-faq__list">
    <li class="collapsable-faq__item">
      <p class="collapsable-faq__question">Can I use it every day?</p>
      <p class="collapsable-faq__answer">Yes, it is designed for daily use.</p>
    </li>
    <li class="collapsable-faq__item">
      <p class="collapsable-faq__question">Will it feel greasy?</p>
      <p class="collapsable-faq__answer">No, it absorbs quickly and is non-sticky.</p>
    </li>
  </ul>
</div></body></html>
"""


def test_extract_faq_pairs_reads_paired_class_markup():
    faqs = extract_faq_pairs(SHOPIFY_ACCORDION_HTML)
    assert len(faqs) == 2
    assert faqs[0]["question"] == "Can I use it every day?"
    assert faqs[0]["answer"] == "Yes, it is designed for daily use."
    assert faqs[1]["question"] == "Will it feel greasy?"
    # every question must actually be a question, not captured answer prose
    assert all(f["question"].endswith("?") for f in faqs)
    assert all("absorbs quickly" not in f["question"] for f in faqs)


def test_load_product_handles_uses_product_media_json():
    media = {
        "SKU_1": {"handle": "product-one"},
        "SKU_2": {"handle": ""},
        "SKU_3": {"handle": "product-three"},
    }
    handles = load_product_handles(media)
    assert handles == ["product-one", "product-three"]
