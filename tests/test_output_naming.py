from app.output_naming import output_name


def test_notice_filename_uses_confirmed_fields_and_option():
    task={'text':'二、命名要求\n命名：姓名+学号+优秀共青团员/干部。','analysis':{}}
    state={'template_name':'申请表.doc','word_format':'docx','option':{'title':'优秀共青团员'},'fields':[
        {'label':'姓 名','value':'测试姓名'},{'label':'学号','value':'20260001'}]}
    assert output_name(task,state)=='测试姓名+20260001+优秀共青团员.docx'


def test_model_pattern_order_and_safe_filename():
    task={'analysis':{'naming_pattern':'{学号}_{姓名}_{申报事项}'}}
    state={'template_name':'申请.doc','word_format':'doc','option':{'title':'干部'},'fields':[
        {'label':'姓名','value':'测/试'},{'label':'学号','value':'1001'}]}
    assert output_name(task,state)=='1001_测_试_干部.doc'


def test_missing_name_does_not_invent_identity():
    task={'analysis':{'naming_pattern':'{姓名}_{学号}'}}
    state={'template_name':'申请.docx','fields':[]}
    assert output_name(task,state)=='申请_已填写.docx'
