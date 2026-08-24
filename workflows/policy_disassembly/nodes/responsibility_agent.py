import os
import sys
import traceback

from config import APP_ENV, GENERAL_TAG_URL, KBTYPE_ID2NAME_MAPPING
from infrastructure.http_session import get_session

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from utils import logger
import json
import json_repair
# from config import propagate_attributes
from repositories.langfuse_integration import (
    extract_responsibility_with_langfuse
)

def responsibility_discern(non_responsibility_text, health_notice_text, policy_no, org_code):
    try:
        result = {}
        if non_responsibility_text == "":
            result["nonResponsibilityList"] = []
        else:
            logger.info(f"条款拆解: policy_no-{str(policy_no)} 正在进行责免拆解")
            # 使用外层已生成的session_id，避免重复生成
            response = extract_responsibility_with_langfuse(
                text=str(non_responsibility_text),
                session_id=None
            )
            # 修复并解析 JSON 字符串
            try:
                repaired = json_repair.loads(response)
                if isinstance(repaired, list):
                    output = repaired
                else:
                    logger.warning(f"条款拆解: policy_no-{str(policy_no)} 责免拆解返回的数据格式不是列表，使用空列表. 原始数据类型: {type(repaired)}")
                    output = []
            except Exception as parse_err:
                logger.error(f"解析 non_responsibility_text 失败: {parse_err}，使用空列表")
                output = []
            result["nonResponsibilityList"] = output

        logger.info(f"条款拆解: policy_no-{str(policy_no)} 健告拆解待开发-使用缺省值")
        result["healthNoticeList"] = []  # 保持原有的缺省逻辑

        #todo: 健告拆解开发完成后，替换上面的缺省值逻辑

        return extract_kb_tag_for_nonresponse_list(result, org_code)

    except Exception as e:
        logger.error(
            f"条款拆解: policy_no-{policy_no} 责免健告或费用范围拆解失败，错误信息: {str(e)}")
        raise

def extract_kb_tag_for_nonresponse_list(result, org_code):
    '''
    {
    "healthNoticeList": [{
        "healthNoticeName": "住院或手术",
        "healthNoticeInfo": "被保险人过去半年内因病住院、手术。"
    },
    {
        "healthNoticeName": "重大疾病",
        "healthNoticeInfo": "被保险人目前或曾经患有癌症（含白血病、淋巴瘤）、脑肿瘤、脑中风、心肌梗死、尿毒症、肝硬化。"
    },...],
    "nonResponsibilityList": ["保健", "预防", "醉酒", "毒品", "康复", "产后恢复", "拔罐", "轮椅", "眼镜", "隐形眼镜", "配镜",
    "假眼", "假肢", "助听器", "遗传性疾病", "先天性畸形", "染色体异常", "残疾", "宫外孕", "药物过敏", "整容手术", "美容", "人工流产",...]
    }
    '''
    try:
        nonreslist = result.get("nonResponsibilityList", [])
        all_input_list = list(set(nonreslist))
        kbtag_output = {}
        new_nonres_list = []

        if all_input_list:
            try:
                kbtag_output = get_general_kb_tags(all_input_list, org_code)
            except Exception as e:
                logger.error(f"获取KB标签失败: {str(e)}")
                raise Exception(f"获取KB标签失败: {str(e)}")

            if kbtag_output:
                try:
                    for kbtypeid in kbtag_output.keys():
                        kbtypename = KBTYPE_ID2NAME_MAPPING.get(kbtypeid, "")
                        tagnamelist = [name for name in kbtag_output.get(kbtypeid, []) if
                                       name and name in all_input_list]
                        tagnamelist = list(set(tagnamelist))
                        kbvaluelist = ",".join(tagnamelist)
                        if kbtypename and kbvaluelist:
                            new_nonres_list.append(
                                {"nonResponsibilityName": kbtypename, "nonResponsibilityInfo": kbvaluelist})
                except Exception as e:
                    logger.error(f"处理KB标签映射失败: {str(e)}")
                    raise Exception(f"处理KB标签映射失败: {str(e)}")

        # 确保 new_nonres_list 包含 KBTYPE_ID2NAME_MAPPING 中的所有值
        existing_names = {item['nonResponsibilityName'] for item in new_nonres_list}
        for kbtypeid, kbtypename in KBTYPE_ID2NAME_MAPPING.items():
            if kbtypename not in existing_names:
                new_nonres_list.append({
                    "nonResponsibilityName": kbtypename,
                    "nonResponsibilityInfo": ""
                })

        result['nonResponsibilityList'] = new_nonres_list
        return result

    except Exception as e:
        logger.error(f"责免KB标签提取失败: {str(e)}")
        raise Exception(f"责免KB标签提取失败: {str(e)}")


# 新接口，输入是潜在的标签文本（不区分标签类型），返回对应的标签id和标签类型
def get_general_kb_tags(input_text_list, org_code="10001"):
    result_map = {}
    try:
        general_kb_enabled = os.getenv('GENERAL_KB_ENABLED', 'true').lower() in {'1', 'true', 'yes', 'on'}
        if os.getenv('LOCAL_DEMO_MODE', 'false').lower() == 'true' or not general_kb_enabled:
            return {}
        session = get_session()
        # 只在个人 Demo调用
        if APP_ENV == 'disabled':
            return {}
        items = [
                    {
                        "tagAliasesName": name
                    }
            for name in input_text_list
        ]
        data = {
            "tenantCode": org_code,
            "paramDTOList": items
        }
        headers = {
            'Content-Type': 'application/json'
        }
        url_response = session.post(GENERAL_TAG_URL, headers=headers, data=json.dumps(data))
        output = url_response.json()
        if output['code'] == '200':
            result_map = parse_tags_by_item_type(output)
        return result_map
    except Exception as e:
        logger.info("【general kb库标签获取报错】兜底处理返回空列表,错误原因:"+str(e))
        print("堆栈跟踪信息:")
        traceback.print_exc()
        return {}

def parse_tags_by_item_type(json_data):
    """
    从JSON数据中解析tags里的item，按照itemType分组，每组存储name的列表
    """
    # 初始化结果字典
    result = {}

    # 遍历JSON数据中的每个tag项
    for tag_item in json_data.get('result', {}).get('paramDTOList', []):
        if not tag_item.get('tags', []):
            continue
        for tag in tag_item.get('tags', []):
            item_type = tag.get('itemType')
            name = tag.get('name')

            # 如果itemType和name都存在
            if item_type is not None and name is not None:
                item_type = str(item_type)
                # 如果该itemType还没有在结果字典中，初始化一个空列表
                if item_type not in result:
                    result[item_type] = []
                # 将name添加到对应itemType的列表中
                result[item_type].append(name)

    return result

# 测试
if __name__ == "__main__":
    non_responsibility_text = """1.因被保险人故意犯罪或抗拒依法采取的刑事强制措施所致的伤害；
2.被保险人自杀、故意自伤所致的伤害；
3.被保险人醉酒、吸食或注射毒品所致的伤
害；
4.被保险人患有精神病、癫痫病所致的伤害；
5.被保险人因妊娠、流产、分娩或人工
流产所致的伤害；
6.被保险人进行整容手术或美容所致的伤害；
7.被保险人进行非医疗必要的手术或治疗所致的伤害
8.被保险人患有下列疾病所致的伤害：
(1)先天性畸形、变形或染色体异常；
(2)遗传性疾病；
(3)残疾"""
    health_notice_text = """"""
    fee_scope_dict = {}
    structure_tree = {}
    policy_no = 123456789
    result = responsibility_discern(non_responsibility_text, health_notice_text, policy_no)
    print(json.dumps(result, ensure_ascii=False, indent=4))
