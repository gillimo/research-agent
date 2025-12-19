from researcher.cli import should_cloud_hop


def test_should_cloud_hop_auto_threshold():
    assert should_cloud_hop("auto", top_score=0.2, threshold=0.3) is True
    assert should_cloud_hop("auto", top_score=0.4, threshold=0.3) is False


def test_should_cloud_hop_always_off():
    assert should_cloud_hop("always", top_score=1.0, threshold=0.3) is True
    assert should_cloud_hop("off", top_score=0.0, threshold=0.3) is False
