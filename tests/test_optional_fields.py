from app.fulfillment import FormField,apply_optional_fields,save_state,get_state,analysis_key
from app.store import Store


def test_model_fields_default_to_optional():
    assert not FormField(target='c1',label='获奖情况-时间').required


def test_award_rows_optional_and_identity_unchanged():
    state={'fields':[{'label':label,'required':True,'value':''} for label in
        ['获奖情况-时间','获奖情况-奖项名称','获奖情况-颁奖单位','获奖情况-时间（第二行）','姓名']]}
    apply_optional_fields(state)
    assert [x['required'] for x in state['fields']]==[False,False,False,False,True]


def test_existing_forms_fixed_without_losing_values_or_revision(tmp_path):
    store=Store(tmp_path/'data');tid,_=store.create_task('m','通知','local',{})
    state={'revision':'keep','analysis_key':analysis_key(store,tid),'fields':[
        {'label':'获奖情况-时间','required':True,'value':'2025.12'}]}
    save_state(store,tid,state)
    result=get_state(store,tid)
    assert result['revision']=='keep' and result['fields'][0]['value']=='2025.12'
    assert not result['fields'][0]['required']
