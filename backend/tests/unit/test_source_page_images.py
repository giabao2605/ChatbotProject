"""Rendered page provenance is immutable and current-snapshot specific."""
import pytest
from sqlalchemy import text
from mech_chatbot.db.repositories import source_page_images as images
from mech_chatbot.db.repositories.structured_evidence import register_source_snapshot
from .test_structured_evidence_repository import store


@pytest.fixture
def image_store(store):
    with store.begin() as conn:
        conn.execute(text('ALTER TABLE StructuredSourceSnapshot ADD PageImagesJson TEXT'))
    register_source_snapshot(1,2,'s1','a'*64,db_engine=store)
    return store


def test_binding_page_hash_to_current_snapshot_and_rejecting_rewrites(image_store):
    images.save_source_page_images(1,2,'s1','a'*64,{1:'b'*64,2:'c'*64},db_engine=image_store)
    assert images.load_source_page_hash(1,2,'s1','a'*64,2,db_engine=image_store)=='c'*64
    with pytest.raises(ValueError):
        images.save_source_page_images(1,2,'s1','a'*64,{1:'d'*64},db_engine=image_store)
    register_source_snapshot(1,2,'s2','d'*64,db_engine=image_store)
    assert images.load_source_page_hash(1,2,'s1','a'*64,2,db_engine=image_store) is None


@pytest.mark.parametrize('value',[{}, {0:'b'*64},{True:'b'*64},{1:'invalid'}, {1:None},[]])
def test_invalid_page_manifest_is_not_registered(image_store,value):
    with pytest.raises(ValueError):
        images.save_source_page_images(1,2,'s1','a'*64,value,db_engine=image_store)
    assert images.load_source_page_hash(1,2,'s1','a'*64,1,db_engine=image_store) is None


def test_missing_schema_and_malformed_manifest_never_imply_verified_image(store,image_store):
    with image_store.begin() as conn:
        conn.execute(text("UPDATE StructuredSourceSnapshot SET PageImagesJson='invalid'"))
    assert images.load_source_page_hash(1,2,'s1','a'*64,1,db_engine=image_store) is None
    with image_store.begin() as conn:
        conn.execute(text('ALTER TABLE StructuredSourceSnapshot DROP COLUMN PageImagesJson'))
    assert images.load_source_page_hash(1,2,'s1','a'*64,1,db_engine=image_store) is None


def test_bindings_must_target_the_current_registered_raw_hash(image_store):
    with pytest.raises(ValueError):
        images.save_source_page_images(1,2,'s1','wrong-source',{1:'b'*64},db_engine=image_store)
    images.save_source_page_images(1,2,'s1','a'*64,{1:'b'*64},db_engine=image_store)
    images.save_source_page_images(1,2,'s1','a'*64,{1:'b'*64},db_engine=image_store)
    assert images.load_source_page_hash(1,3,'s1','a'*64,1,db_engine=image_store) is None
