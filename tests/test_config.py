from glg import config


def test_paths_are_under_data_dir():
    for p in (config.RAW_DIR, config.INTERIM_DIR, config.PROCESSED_DIR, config.OUTPUTS_DIR):
        assert p.is_relative_to(config.DATA_DIR)


def test_reference_dir_is_in_repo():
    assert config.REFERENCE_DIR.is_relative_to(config.PROJECT_ROOT)
