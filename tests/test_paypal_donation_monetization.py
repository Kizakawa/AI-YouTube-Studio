import pytest
from app.services.monetization_engine import (
    build_paypal_donation_block,
    enrich_video_monetization,
    build_repurposed_video_description,
)

def test_build_paypal_donation_block_with_url():
    """Kiểm tra sinh khối quyên góp PayPal khi có URL trực tiếp."""
    cfg = {
        "id": "ch_paws_hearts_rescue",
        "niche": "animal_rescue",
        "paypal_url": "https://paypal.me/pawsrescue",
    }
    block = build_paypal_donation_block(cfg, niche="animal_rescue")
    assert "SUPPORT OUR RESCUE MISSIONS & VET CARE" in block
    assert "https://paypal.me/pawsrescue" in block
    assert "Donate directly via PayPal" in block
    assert "Bio" in block

def test_build_paypal_donation_block_fallback_bio():
    """Kiểm tra sinh khối quyên góp khi chưa có URL (kêu gọi bấm link Bio kênh)."""
    cfg = {
        "id": "ch_wild_odyssey",
        "niche": "wildlife_predators",
        "paypal_url": "",
    }
    block = build_paypal_donation_block(cfg, niche="wildlife_predators")
    assert "WILDLIFE EXPEDITIONS" in block
    assert "PayPal link in Bio" in block

def test_build_paypal_donation_block_psychology():
    """Kiểm tra khối ủng hộ cho ngách Tâm lý học (Psychology)."""
    cfg = {
        "id": "channel_maya",
        "niche": "psychology",
        "paypal_url": "https://paypal.me/psychworld",
    }
    block = build_paypal_donation_block(cfg, niche="psychology")
    assert "SUPPORT INDEPENDENT RESEARCH" in block
    assert "https://paypal.me/psychworld" in block

def test_enrich_video_monetization_both_donation_and_affiliate():
    """Kiểm tra mô tả video chứa cả khối kêu gọi ủng hộ PayPal và sản phẩm Amazon."""
    cfg = {
        "id": "ch_paws_hearts_rescue",
        "niche": "animal_rescue",
        "paypal_url": "https://paypal.me/pawsrescue",
        "amazon_tag": "kizakawa-20",
        "affiliate_enabled": True,
        "enable_donation": True,
    }
    title = "Tiny Puppy Crying Under The Cold Bridge #Shorts"
    desc = "Watch this incredible rescue journey."
    
    enriched_desc, enriched_comment, prod_info = enrich_video_monetization(
        channel_config=cfg,
        title=title,
        description=desc,
        enable_affiliate=True,
        enable_donation=True,
    )
    
    # Phải có cả 2 khối
    assert "SUPPORT OUR RESCUE MISSIONS & VET CARE" in enriched_desc
    assert "https://paypal.me/pawsrescue" in enriched_desc
    assert "Featured Trend & Gear" in enriched_desc
    assert "amazon.com" in enriched_desc
    assert prod_info is not None

def test_enrich_video_monetization_donation_only():
    """Kiểm tra kênh tắt affiliate nhưng bật donation: chỉ có khối ủng hộ, không dính link mua đồ."""
    cfg = {
        "id": "ch_wild_odyssey",
        "niche": "wildlife_predators",
        "paypal_url": "https://paypal.me/wildodyssey",
        "affiliate_enabled": False,
        "enable_donation": True,
    }
    title = "Apex Predator Ambush in Arctic Snow #Shorts"
    desc = "Nature is brutal and fascinating."
    
    enriched_desc, enriched_comment, prod_info = enrich_video_monetization(
        channel_config=cfg,
        title=title,
        description=desc,
        enable_affiliate=False,
        enable_donation=True,
    )
    
    assert "WILDLIFE EXPEDITIONS" in enriched_desc
    assert "https://paypal.me/wildodyssey" in enriched_desc
    # Không được có link amazon
    assert "amazon.com" not in enriched_desc
    assert "Featured Trend & Gear" not in enriched_desc
    assert prod_info is None

def test_build_repurposed_video_description_includes_paypal():
    """Kiểm tra mô tả video tái chế (Mashup/Repurpose) tự động tích hợp PayPal donation."""
    desc = build_repurposed_video_description(
        title="Terrifying Wolf Pack Stalking Bison",
        summary="A breathless wildlife sequence",
        niche_id="wildlife_predators",
        channel_name="Wild Odyssey",
        paypal_url="https://paypal.me/wildodyssey",
        enable_affiliate=False,
        enable_donation=True,
    )
    assert "WILDLIFE EXPEDITIONS" in desc
    assert "https://paypal.me/wildodyssey" in desc
    assert "#Shorts" in desc
