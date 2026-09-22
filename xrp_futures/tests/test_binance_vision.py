from xrp_futures.data.binance_vision import FUNDING_COLUMNS, KLINE_COLUMNS, parse_archive_csv


def test_parse_archive_csv_with_header_row():
    raw = (
        b"open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        b"taker_buy_volume,taker_buy_quote_volume,ignore\n"
        b"1578297600000,0.1970,0.2041,0.1970,0.2041,6306.4,1578301199999,1243.65622,4,194.8,39.67102,0\n"
    )
    df = parse_archive_csv(raw, KLINE_COLUMNS)
    assert list(df.columns) == KLINE_COLUMNS
    assert df.iloc[0]["open_time"] == 1578297600000
    assert df.iloc[0]["open"] == 0.1970


def test_parse_archive_csv_without_header_row_regression():
    # This is the exact shape of Binance's pre-2021 monthly archives: no header row.
    # Before the fix, pd.read_csv() silently promoted this data row to column names,
    # dropped every year of early XRPUSDT history with NO error surfaced anywhere.
    raw = (
        b"1578297600000,0.1970,0.2041,0.1970,0.2041,6306.4,1578301199999,1243.65622,4,194.8,39.67102,0\n"
        b"1578301200000,0.2013,0.2222,0.2004,0.2127,6676329.3,1578304799999,1388223.65941,1406,2537247.0,530526.06882,0\n"
    )
    df = parse_archive_csv(raw, KLINE_COLUMNS)
    assert list(df.columns) == KLINE_COLUMNS
    assert len(df) == 2
    assert df.iloc[0]["open_time"] == 1578297600000
    assert df.iloc[0]["open"] == 0.1970
    assert df.iloc[1]["open_time"] == 1578301200000


def test_parse_archive_csv_funding_with_and_without_header():
    with_header = b"calc_time,funding_interval_hours,last_funding_rate\n1672531200000,8,0.00010000\n"
    without_header = b"1672531200000,8,0.00010000\n"

    df1 = parse_archive_csv(with_header, FUNDING_COLUMNS)
    df2 = parse_archive_csv(without_header, FUNDING_COLUMNS)

    assert list(df1.columns) == FUNDING_COLUMNS == list(df2.columns)
    assert df1.iloc[0]["last_funding_rate"] == df2.iloc[0]["last_funding_rate"] == 0.0001
