import install


def test_own_lines_are_replaced_and_others_kept() -> None:
    current = "\n".join(
        [
            "7 * * * * run-rental-finder # rental-finder hourly scan",
            "*/5 * * * * bash /old/buy-assist/scripts/serve.sh # buy-assist keepalive",
            "0 1 * * * echo 'not ours, mentions # buy-assistant elsewhere'",
        ]
    )

    merged = install.merged_crontab(current, ["@reboot new # buy-assist start at boot"])

    assert merged.splitlines() == [
        "7 * * * * run-rental-finder # rental-finder hourly scan",
        "0 1 * * * echo 'not ours, mentions # buy-assistant elsewhere'",
        "@reboot new # buy-assist start at boot",
    ]


def test_repo_path_is_quoted_in_cron_commands() -> None:
    assert all("bash '" in line or "bash /" in line for line in install.cron_lines())
