import pytest
from app import form_assistant as a


def setup(monkeypatch, answer):
    state={'revision':'r','fields':[
        {'target':'rank','label':'排名百分比','original':'','value':''},
        {'target':'summary','label':'个人总结','help':'150字以内','original':'示例','value':'示例'}]}
    monkeypatch.setattr(a,'get_state',lambda *args:state)
    monkeypatch.setattr(a,'model_json',lambda *args:a.Suggestion(**answer))
    return {'rank':'','summary':'已填写的真实事实'}


def test_rank_is_calculated_on_server(monkeypatch):
    values=setup(monkeypatch,dict(reply='',rank_target='rank',rank_position=2,rank_total=30))
    result=a.suggest(None,'t',None,'r',values,'第2名，共30人')
    assert result['updates']==[{'target':'rank','value':'6.67%'}]


def test_clarification_does_not_change_fields(monkeypatch):
    values=setup(monkeypatch,dict(reply='请问是第2名共30人，还是排名区间？'))
    assert not a.suggest(None,'t',None,'r',values,'二到三十')['updates']


@pytest.mark.parametrize('answer',[
    dict(reply='',updates=[dict(target='unknown',value='x')]),
    dict(reply='',updates=[dict(target='summary',value='字'*151)]),
    dict(reply='',rank_target='rank',rank_position=31,rank_total=30),
])
def test_invalid_model_patches_are_rejected(monkeypatch,answer):
    values=setup(monkeypatch,answer)
    with pytest.raises(ValueError):a.suggest(None,'t',None,'r',values,'帮我填写')


def test_stale_form_is_rejected(monkeypatch):
    values=setup(monkeypatch,dict(reply=''))
    with pytest.raises(ValueError):a.suggest(None,'t',None,'old',values,'填写')


def test_wrong_model_target_is_rerouted_to_percentage(monkeypatch):
    state={'revision':'r','fields':[
        {'target':'ratio','label':'排名/总人数'},
        {'target':'pct','label':'排名百分比'}]}
    monkeypatch.setattr(a,'get_state',lambda *args:state)
    monkeypatch.setattr(a,'model_json',lambda *args:a.Suggestion(reply='',rank_target='ratio',rank_position=2,rank_total=30))
    result=a.suggest(None,'t',None,'r',{'ratio':'2/30','pct':'(1.7%)'},'我的排名2/30')
    assert {x['target']:x['value'] for x in result['updates']}=={'ratio':'2/30','pct':'6.67%'}


def test_combined_field_keeps_rank_and_percentage(monkeypatch):
    monkeypatch.setattr(a,'get_state',lambda *args:{'revision':'r','fields':[{'target':'both','label':'排名/总人数（百分比）'}]})
    monkeypatch.setattr(a,'model_json',lambda *args:a.Suggestion(reply='',rank_target='both',rank_position=2,rank_total=30))
    result=a.suggest(None,'t',None,'r',{'both':''},'排名2/30')
    assert result['updates']==[{'target':'both','value':'2/30（6.67%）'}]


def test_no_percentage_field_does_not_overwrite_ratio(monkeypatch):
    monkeypatch.setattr(a,'get_state',lambda *args:{'revision':'r','fields':[{'target':'ratio','label':'排名/总人数'}]})
    monkeypatch.setattr(a,'model_json',lambda *args:a.Suggestion(reply='',rank_target='ratio',rank_position=2,rank_total=30))
    result=a.suggest(None,'t',None,'r',{'ratio':'2/30'},'排名2/30')
    assert result['updates']==[] and '6.67%' in result['reply']


def test_ambiguous_percent_fields_ask_instead_of_guessing(monkeypatch):
    state={'revision':'r','fields':[{'target':'ratio','label':'排名/总人数'},
        {'target':'study','label':'学习排名百分比'},{'target':'overall','label':'综合排名百分比'}]}
    monkeypatch.setattr(a,'get_state',lambda *args:state)
    monkeypatch.setattr(a,'model_json',lambda *args:a.Suggestion(reply='',rank_target='ratio',rank_position=2,rank_total=30))
    result=a.suggest(None,'t',None,'r',{'ratio':'2/30','study':'','overall':''},'排名2/30')
    assert not result['updates'] and '明确' in result['reply']


def test_unstructured_percentage_cannot_overwrite_rank(monkeypatch):
    monkeypatch.setattr(a,'get_state',lambda *args:{'revision':'r','fields':[{'target':'ratio','label':'排名/总人数'}]})
    monkeypatch.setattr(a,'model_json',lambda *args:a.Suggestion(reply='',updates=[a.Patch(target='ratio',value='6.67%')]))
    with pytest.raises(ValueError,match='不能把百分比'):a.suggest(None,'t',None,'r',{'ratio':'2/30'},'帮我算百分比')
