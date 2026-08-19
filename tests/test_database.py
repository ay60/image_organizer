from organizer.database import Database


def test_add_and_remove_only_affects_database(tmp_path):
    image = tmp_path / "image.jpg"
    image.write_bytes(b"an image placeholder")
    database = Database(tmp_path / "organizer.sqlite3")

    thumbnail = b"thumbnail bytes"
    assert database.add_image(image, thumbnail) is True
    assert database.add_image(image, thumbnail) is False
    records = database.list_images()
    assert len(records) == 1
    assert records[0].path == str(image.resolve())
    assert records[0].thumbnail == thumbnail

    database.remove_image(records[0].id)
    assert database.list_images() == []
    assert image.exists()


def test_deleting_image_cascades_its_image_tag_links(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    database = Database(tmp_path / "organizer.sqlite3")
    database.add_image(first, b"first thumbnail")
    database.add_image(second, b"second thumbnail")
    records_by_name = {record.filename: record.id for record in database.list_images()}
    first_id = records_by_name["first.jpg"]
    second_id = records_by_name["second.jpg"]

    database.add_tag_to_image(first_id, "holiday")
    database.add_tag_to_image(second_id, "holiday")
    database.add_tag_to_image(first_id, "family")
    assert database.image_ids_for_all_tags(["holiday", "family"]) == [first_id]

    database.remove_image(first_id)

    assert database.tag_names_for_image(first_id) == []
    assert database.tag_names_for_image(second_id) == ["holiday"]
    assert database.image_ids_for_all_tags(["holiday"]) == [second_id]
    assert first.exists()


def test_list_images_filters_to_the_intersection_of_tags(tmp_path):
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    database = Database(tmp_path / "organizer.sqlite3")
    database.add_image(first, b"first thumbnail")
    database.add_image(second, b"second thumbnail")
    ids = {record.filename: record.id for record in database.list_images()}
    database.add_tag_to_image(ids["first.jpg"], "holiday")
    database.add_tag_to_image(ids["first.jpg"], "family")
    database.add_tag_to_image(ids["second.jpg"], "holiday")

    assert [image.filename for image in database.list_images(["holiday", "family"])] == ["first.jpg"]
