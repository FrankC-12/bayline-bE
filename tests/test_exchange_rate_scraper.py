"""Regression coverage for the BCV HTML parser — the fragile part of the
exchange-rate feature, since it depends on the exact markup of a page we
don't control. A silent regex mismatch here is exactly the failure mode
that leaves the rate stuck at a stale value with no visible error (the
refresh loop only logs and retries). Fixture below mirrors BCV's real
current structure (verified 2026-09-11) rather than an idealized one."""

import pytest

from app.modules.exchange_rates.service import parse_bcv_html

BCV_HTML = """
<html>
<body>
<span class="pull-right dinpro center">
Fecha Valor: <span class="date-display-single" property="dc:date" datatype="xsd:dateTime" content="2026-09-11T00:00:00-04:00">Viernes, 11 Septiembre 2026</span>
</span>
<div id="dolar" class="col-sm-12 col-xs-12 ">
    <div class="field-content">
        <div class="row recuadrotsmc">
            <div class="col-sm-6 col-xs-6">
                <img src="/sites/default/files/dollar-04_2.png" class="icono_bss_blanco1">
                <span> USD</span>
            </div>
            <div class="col-sm-6 col-xs-6 centrado textp"> <strong class="strong-tb">832,48830000</strong> </div>
        </div>
    </div>
</div>
<div id="euro" class="col-sm-12 col-xs-12 ">
    <div class="field-content">
        <div class="row recuadrotsmc">
            <div class="col-sm-6 col-xs-6">
                <img src="/sites/default/files/euro-04_2.png" class="icono_bss_blanco1">
                <span> EUR </span>
            </div>
            <div class="col-sm-6 col-xs-6 centrado textp"><strong class="strong-tb"> 968,06734453</strong></div>
        </div>
    </div>
</div>
</body>
</html>
"""


def test_parse_bcv_html_extracts_date_and_both_rates():
    value_date, rates = parse_bcv_html(BCV_HTML)
    assert value_date.isoformat() == "2026-09-11"
    assert rates == {"USD": 832.4883, "EUR": 968.06734453}


def test_parse_bcv_html_raises_without_a_value_date():
    document = BCV_HTML.replace('property="dc:date"', "")
    with pytest.raises(ValueError, match="value date"):
        parse_bcv_html(document)


def test_parse_bcv_html_raises_when_a_currency_widget_is_missing():
    document = BCV_HTML.split('<div id="euro"')[0] + "</body></html>"
    with pytest.raises(ValueError, match="EUR"):
        parse_bcv_html(document)


def test_parse_bcv_html_tolerates_html_entities_in_surrounding_markup():
    document = BCV_HTML.replace("Septiembre", "Septiembre&nbsp;")
    value_date, rates = parse_bcv_html(document)
    assert value_date.isoformat() == "2026-09-11"
    assert rates["USD"] == 832.4883
