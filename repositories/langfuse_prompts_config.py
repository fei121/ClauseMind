"""
Prompt 配置文件
为所有 LLM 调用提供 prompt 配置模板
"""
import os


def _get_langfuse_connection_config():
    """Return the credentials required by the prompt management CLI."""
    config = {
        "host": os.getenv("LANGFUSE_HOST", "").rstrip("/"),
        "public_key": os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        "secret_key": os.getenv("LANGFUSE_SECRET_KEY", ""),
    }
    missing = [name for name, value in config.items() if not value]
    if missing:
        raise RuntimeError(
            "缺少 Langfuse 配置: "
            + ", ".join(f"LANGFUSE_{name.upper()}" for name in missing)
        )
    return config


def _create_langfuse_client():
    """Create a Langfuse client for importing or exporting local prompts."""
    from langfuse import Langfuse

    return Langfuse(**_get_langfuse_connection_config())

# ================== 1. 特别约定索引查找 (extract_general_audit_items) ==================
SPECIAL_AGREEMENT_INDEX_PROMPT = {
    "name": "special_agreement_index",
    "description": "从保险条款目录中提取特别约定对应的文本索引",
    "template": """# 1. 任务背景
你是一个保险审核专家, 你需要根据给定的保险条款目录结构，从保险条款目录中提取出**特别约定**、**备注**、**指定医院（认可医院）**对应的全部文本索引，如[30,32]对应30、31、32，注意如果是一个大标题，那么返回其下所有小标题的索引。未找到对应的索引则返回空列表。

# 2. 输入数据
保险条款目录:
{{index_content}}

# 3. 输出格式规范
先对问题进行分析，给出你的思考，同时你的返回应该包含一个JSON数组列表，数组中每个元素是一个JSON对象，对象包含两个字段：index（整数类型，表示文本索引），title（字符串类型，表示标题名称）。格式如下：
```json
[
    {"index": 1, "title": "## 总单特别约定"},
    {"index": 2, "title": "### 总单特别约定-小标题1"},
    {"index": 3, "title": "# 指定医院"}
]
```
---
**请开始根据上述逻辑分析目录索引并返回JSON结果：**""",
    "input_variables": ["index_content"],
    "output_format": "JSON数组，包含index和title字段"
}

RESPONSIBILITY_INDEX_EXTRACTION_PROMPT = {
    "name": "responsibility_discern_index",
    "description": "从保险条款目录中提取责任免除条款对应的文本索引",
    "template": """你是一个保险审核专家, 现给出保险条款目录，你需要从保险条款目录中提取出**{{clause}}**保险条款的**{{liability}}**保险责任对应的全部'责任免除'和'特别约定'索引，如[30,32]对应30、31、32。
请严格按照要求的格式返回结果，不要添加任何多余的信息。注意对应匹配保险条款及保险责任。未找到对应的索引则返回空列表。

保险条款目录：
{{catalog_content}}

输出格式规范：
先对问题进行分析，给出你的思考，同时你的返回应该包含一个JSON数组列表，数组中每个元素是一个JSON对象，对象包含两个字段：index（整数类型，表示文本索引），title（字符串类型，表示标题名称）。格式如下：
```json
[
    {"index": 30, "title": "责任免除"},
    {"index": 31, "title": "责任免除"},
    {"index": 32, "title": "责任免除"},
    {"index": 45, "title": "总单特别约定"}
]
```

---
**请开始根据上述逻辑分析目录索引并最终返回JSON结果：**""",
    "input_variables": ["clause", "liability", "catalog_content"],
    "output_format": "JSON数组，包含index和title字段"
}


# ================== 11. 保险责任索引查找 (extract_general_audit_items) ==================
LIABILITY_INDEX_EXTRACTION_PROMPT = {
    "name": "liability_index_extraction",
    "description": "从保险条款目录中提取保险责任对应的索引",
    "template": """# Role: 资深保险审核专家

## 1. 任务背景
你需要根据给定的保险条款目录结构，利用**分级检索策略**，提取指定保险责任对应的**全部**相关索引。

## 2. 输入数据
请基于以下变量进行检索：
*   **计划名称**: `{{plan}}`
*   **条款名称**: `{{clause}}`
*   **保险责任**: `{{liability}}`

## 3. 提取维度
请提取包含以下内容的索引（Index）和标题（Title）：
1.  **[保险责任]**: 条款正文中定义该责任的章节（如‘保险责任’、具体责任条目）。
2.  **[保障方案]**: 保单明细表或协议中引用该险种及保障内容的标题/章节。
3.  **[医院定义]**: 条款释义部分关于‘医院’的定义索引。
4.  **[指定/认可医院]**: 特别约定或协议中关于‘指定医院’、‘认可医院’的索引。

## 4. 检索逻辑与回退机制
**重要：** 请严格按照以下优先级顺序执行，**一旦在某一步骤找到匹配项，立即停止进入下一步骤，仅输出当前步骤的提取结果。**

### 步骤一：精确全匹配
*   **范围**: 寻找目录中包含 `{{plan}}` 层级的内容。
*   **动作**: 在该计划层级下，寻找 `{{clause}}` 及其 `{{liability}}`。
*   **判断**:
    *   **若找到**: 仅提取该计划下的所有相关索引 -> **[输出结果并结束]**
    *   **若未找到**: 忽略计划名称 -> **[进入步骤二]**

### 步骤二：险种责任匹配
*   **范围**: 扫描整个目录范围（不限制于特定计划下）。
*   **动作**: 寻找 `{{clause}}`，并在该险种下锁定 `{{liability}}`。
*   **判断**:
    *   **若找到**: 提取该险种下的所有相关索引 -> **[输出结果并结束]**
    *   **若未找到**: -> **[进入步骤三]**

### 步骤三：模糊与语义匹配
*   **范围**: 整个目录。
*   **动作**:
    1.  放弃精确名称匹配，寻找语义上高度近似的险种。
    2.  寻找语义上近似的责任。
*   **规则**: 提取最可能的匹配项 -> **[输出结果并结束]**

---
## 5. 待处理目录数据
```text
{{index_content}}
```

## 6. 输出格式规范
先对问题进行分析，给出你的思考，同时你的返回应该包含一个JSON数组列表，数组中每个元素是一个JSON对象，对象包含两个字段：index（整数类型，表示文本索引），title（字符串类型，表示标题名称）。格式如下：
```json
[
    {"index": 1, "title": "保险责任"},
    {"index": 2, "title": "具体责任条目"},
    {"index": 3, "title": "医院"},
    {"index": 4, "title": "指定医院"}
]
```

---
**请开始根据上述逻辑分析目录索引并最终返回JSON结果：**""",
    "input_variables": ["clause", "liability", "index_content"],
    "output_format": "JSON数组，包含index和title字段"
}

# ================== 12. 等待期抽取 (extract_general_audit_items) ==================
WAITING_PERIOD_EXTRACTION_PROMPT_NEW = {
    "name": "waiting_period_extraction",
    "description": "从保险条款中抽取等待期相关文本",
    "template": """你是一个经验丰富的保险审核专家，请你抽取保险条款中的等待期相关文本：
---
条款文本：
{{block}}
---
保单责任结构表：
{{structure_tree}}
---
抽取信息：
抽取精简的等待期相关文本片段，例如"...重疾、轻症等待期30天..."

抽取要求：
1. 保留文本来源，不同责任对应的既往症约束不同，注意保留正文中提到的相关责任上下文，可以参考上文的保单责任结构表
2. 不要对原文做任何修改，直接返回原文
3. 如果没有相关内容，请返回"未找到相关文本"

你的抽取结果：""",
    "input_variables": ["block", "structure_tree"],
    "output_format": "原文文本或'无'"
}

# ================== 14. 既往症抽取 (extract_general_audit_items) ==================
PAST_ILLNESS_EXTRACTION_PROMPT_NEW = {
    "name": "past_illness_extraction",
    "description": "从保险条款中抽取既往症相关文本",
    "template": """你是一个经验丰富的保险审核专家，请你抽取保险条款中的既往症相关的全部文本：
---
条款文本：
{{block}}
---
保单责任结构表：
{{structure_tree}}
---
抽取信息：
抽取精简的既往症相关文本，例如"...1）疾病身故：承担一般既往症，除外严重既往症。2）重大疾病：参照重大疾病续保的约定。3）意外险/医疗险正常承担所有既往症。4）公共保额：正常承担。..."、"...严重既往症：恶性肿瘤、心脏病（心功能不全Ⅱ级（含）以上）、心肌梗塞..."

抽取要求：
1. 保留文本来源，不同责任对应的既往症约束不同，注意保留正文中提到的相关责任上下文，可以参考上文的保单责任结构表
2. 不要对原文做任何修改，直接返回原文
3. 如果没有相关内容，请返回"未找到相关文本"

你的抽取结果：""",
    "input_variables": ["block", "structure_tree"],
    "output_format": "原文文本或'无'"
}

# ================== 15. 修改基础情形抽取 (extract_general_audit_items) ==================
MULTI_SCENARIO_EXTRACTION_PROMPT = {
    "name": "multi_scenario_extraction",
    "description": "从特别约定中抽取修改基础情形的相关文本",
    "template": """你是一个经验丰富的保险审核专家，请你抽取特别约定中的相关文本：
特别约定：
---
{{block}}
---
抽取信息要点：
1、抽取关于指定医院的包含增加和除外约定，例如"...二级及二级以上医保定点公立医院内就诊治疗..."、"不包括...医院"、"增加...为指定医院"
2、抽取"急诊情况"下对于医院范围的新约定，例如"被保险人遇本协议约定之急诊情况时，可..."
3、抽取关于"北京市医疗保险"相关的医院范围约定，例如"参加北京市基本医疗保险的被保险人的..."
4、抽取关于"特诊"或"特需"的"医保结算"新约定，例如"所有被保险人如在特诊（特需）病房住院..."、"...层级的被保险人如在特诊（特需）病区就诊..."
5、抽取关于某个计划的医院范围约定，例如"高管层级开放...为其指定医院"对应高管计划。

抽取要求：
不要对原文做任何修改，直接返回原文。
每个抽取信息要点可能会存在多段文本符合要求，请全部返回，且每段文本单独成行。
如果没有相关内容，请返回"无"。

你的抽取结果：""",
    "input_variables": ["block"],
    "output_format": "每行一段原文文本或'无'"
}

# ================== 16. 通用赔付范围生成 (extract_general_audit_items) ==================
BASE_COMPENSATION_GENERATION_PROMPT = {
    "name": "base_compensation_generation",
    "description": "根据保险责任和生成基础赔付情形",
    "template": """## 任务说明
你是一名经验丰富的健康保险理赔专家，负责根据提供的保险条款段落和特定的保险责任，精确提取该保险责任在理赔时可依据的赔付情形说明，并按要求格式化输出。

## 输入
- **保险责任段落**：
{{liability_paragraph}}
- **责任关键词**（计划名称_条款名称_责任名称）：
{{liability_keyword}}

## 字段提取说明
{
  "事故类型": {
    "required": true,
    "options": ["疾病", "意外"],
    "default": "疾病、意外",
    "description": "从保险责任段落抽取。"
  },
  "治疗类型": {
    "required": true,
    "options": [
      "普通门诊",
      "急诊",
      "专家门诊",
      "特病门诊",
      "特需门诊",
      "国际部门诊",
      "住院",
      "ICU病房",
      "特需病房",
      "家庭病床",
      "国际部住院"
    ],
    "default": "普通门诊、急诊、专家门诊、特病门诊、住院、ICU病房、特需病房",
    "description": "从保险责任段落部分抽取。注意门诊或急诊默认对应"普通门诊、急诊、专家门诊、特病门诊"，住院默认对应"住院、ICU病房、特需病房""
  },
  "医院范围": {
    "required": true,
    "options": [],
    "default": "医保定点二级以上（含二级）公立医院、{指定医院除外：XXX}、{指定医院包含：YYY}",
    "description": "提取允许理赔的医疗机构类型/等级（如二级或二级以上的医保定点公立医院等）；指定医院除外、指定医院包含必须用花括号格式输出；注意：若文本中出现具体的医院列表，必须将列表中的具体医院名称逐一提取出来，用顿'、'连接，放入'{指定医院包含：...}'中"
  }
}


## 提取步骤。
可以从责任对应保险计划的"保险责任段落"中提取该保单通用的基本赔付医院范围。
住院治疗情形默认需要扩展为：住院、ICU病房、特需病房
门诊治疗情形默认需要扩展为：普通门诊, 急诊, 专家门诊, 特病门诊, 特需门诊

## 输出格式示例
### 示例1
[
    {
      "事故类型": "疾病、意外",
      "治疗类型": "普通门诊、急诊、专家门诊、特病门诊、特需门诊、住院、ICU病房、特需病房",
      "医院范围": "二级以上（含二级）公立医院",
    }
]
### 示例2
[
    {
      "事故类型": "疾病、意外",
      "治疗类型": "普通门诊、急诊、专家门诊、特病门诊、特需门诊",
      "医院范围": "医保定点公立医院",
    }
]

## 输出要求
1. **严格提取信息**：严格按照"字段提取说明"提取信息，不添加任何额外字段。对于未提及的字段，使用默认值（如字段提取说明中定义）。
2. **格式化要求**：输出严格参考示例格式，不参考内容：输出一个 JSON 数组，包含"字段提取说明"的字段，每个字段是一个字符串，多个值使用"、"连接。如果某个字段没有相关描述，则该字段值为空字符串。
3. **输出仅结果**：只输出格式化结果，不包含任何```json```等字样，不要对输出的内容做任何解释说明。

## 你的输出
""",
    "input_variables": ["liability_paragraph", "liability_keyword"],
    "output_format": "JSON数组"
}

# ================== 17. 多情形赔付范围生成 (extract_general_audit_items) ==================
MULTI_COMPENSATION_GENERATION_PROMPT = {
    "name": "multi_compensation_generation",
    "description": "基于基础赔付情形和特别约定生成多情形赔付范围",
    "template": """# 任务说明
你是一名经验丰富的健康保险理赔专家，负责根据提供的保险"补充情形文本段落"和特定的"保险责任"，修改"基础保险责任"在理赔时可依据的赔付情形说明，并按要求格式化输出。

# 字段提取说明
{
  "事故类型": {
    "required": true,
    "options": ["疾病", "意外"],
    "default": "疾病、意外",
    "description": "从保险责任段落抽取。"
  },
  "治疗类型": {
    "required": true,
    "options": [
      "普通门诊", "急诊", "专家门诊", "特病门诊", "特需门诊",
      "国际部门诊", "住院", "ICU病房", "特需病房", "家庭病床", "国际部住院"
    ],
    "default": "普通门诊、急诊、专家门诊、特病门诊、住院、ICU病房、特需病房",
    "description": "从保险责任段落部分抽取。注意门诊或急诊默认对'普通门诊、急诊、专家门诊、特病门诊'，住院默认对应'住院、ICU病房、特需病房'"
  },
  "医院范围": {
    "required": true,
    "options": [],
    "default": "医保定点二级以上（含二级）公立医院、{指定医院除外：XXX}、{指定医院包含：YYY}",
    "description": "提取允许理赔的医疗机构类型/等级（如二级或二级以上的医保定点公立医院等）；指定医院除外、指定医院包含必须用花括号格式输出；注意：若文本中出现具体的医院列表，必须将列表中的具体医院名称逐一提取出来，用顿'、'连接，放入'{指定医院包含：...}'中"
  },
  "发票": {
    "required": false,
    "options": ["(社保账单：是)", "(统筹支付：是)"],
    "default": "",
    "description": "仅当文本明确规定'未使用医保卡/社保卡则不予理赔'或'必须使用医保卡/社保卡就诊'等'非此不可'的强制性条件时，记录'发票：(社保账单：是)'。若文本表述为'未使用医保需扣除一定比例/金额后赔付'或'未使用医保按xx比例赔付'（即允许理赔但降低赔付额），则视为非强制条件，该字段保持默认空值，不要填写。获得统筹支付要求同样记录'(统筹支付：是)'"
  }
}

# 输入
- **基础保险责任**：
{{base_liability_json}}
- **特别约定补充情形文本段落**：
{{supplement_text}}
- **保单责任结构表**：
{{structure_tree}}
- **本次处理的保险责任**（计划名称_条款名称_责任名称）：
{{current_liability}}

# 修改逻辑
首先识别特别约定补充情形文本段落是否属于对应保险责任的条款段落，需要匹配计划名称、条款名称和责任名称，再修改计划对应的提取字段。

然后根据每行特别约定补充情形文本段落，参考以下逻辑修改基础情形的字段：

**核心原则：**
如果特别约定中针对某种“治疗类型”（如“急诊”、“特需门诊/病房”）规定了与基础情形**不一致**的规则（如医院范围不同、发票要求不同），则必须触发**情形拆分**：
1. **新增情形**：为该治疗类型创建一个独立的配置条目，使用特约规定的规则。
2. **修正基础情形**：从基础情形的“治疗类型”字段中**删除**该治疗类型，确保规则不冲突。
   - *例如*：基础为“住院、急诊”，特约说“急诊可以去非指定医院”，则输出变为：情形1（住院），情形2（急诊+非指定医院）。

**具体字段修改示例：**
- "被保险人如在特诊（特需）病区就诊，不强制使用医保卡或社保卡。"
    - **情形拆分**：触发拆分，针对“特需门诊/特需病房”。
    - **基础情形**：治疗类型删除“特需门诊/特需病房”。
    - **新增情形**：治疗类型为“特需门诊/特需病房”，发票置空。
    - **医院范围**：将{指定医院除外：XXX}中的特诊（特需）项删除。

- "被保险人遇本协议约定之急诊情况时，可就近选择(社保定点)公立医院治疗"
    - **情形拆分**：触发拆分，针对“急诊”。
    - **基础情形**：治疗类型删除“急诊”。
    - **新增情形**：治疗类型为“急诊”。
    - **医院范围**：限定为"(医保定点)公立医院"（根据文本具体提取）。
    - **发票**：根据文本提取。

- "所有被保险人如在特诊（特需）病房住院，在有医保结算的前提下，保险人可以按其保险责任范围和比例给予正常理赔。"
    - **情形拆分**：触发拆分，针对“特需病房”。
    - **基础情形**：治疗类型删除“特需病房”。
    - **新增情形**：治疗类型为“特需病房"，发票字段补充"发票：（社保账单：是）"。

**通用规则（适用于所有情形）：**
- 如条款中提到"医院涵盖范围为二级及二级以上医院或医保定点公立医院，但不包括医保定点公立医院中的康复医院、职工医院、联合诊所、民办医院，也不包括主要作为诊所、康复、护理、休养、静养、戒酒、戒毒等或类似的医疗机构"，则基本情形的医院范围为"二级及二级以上医院或医保定点公立医院、{指定医院除外：医保定点公立医院中的康复医院、职工医院、联合诊所、民办医院、诊所、康复、护理、休养、静养、戒酒、戒毒等或类似的医疗机构}"。
- **医院范围提取完整性要求**：在提取排除项时，请务必仔细扫描文本，确保不遗漏任何被排除的机构名词（如“诊所”、“康复中心”等），即使它们在句子中作为修饰语出现。
- **社保强制判定规则**：判断是否需要填写“(社保账单：是）”时，请严格区分“免责/拒赔”与“比例赔付/扣除费用”。
  - 情况A（强制）：条款中出现“未使用社保不予理赔”、“必须在定点医院使用社保结算”等表述 -> 记录 `发票：(社保账单：是）`。
  - 情况B（不强制）：条款中出现“未使用社保需扣除xx”、“未使用社保按xx%赔付”等表述 -> **不记录**发票字段（保持为空），因为该情形下未使用社保依然可以申请理赔。

# 输出格式示例
每个情形作为输出列表中的字典元素：
```json
[
  {
    "事故类型": "疾病、意外",
    "治疗类型": "普通门诊、专家门诊、特病门诊",
    "医院范围": "医保定点二级以上（含二级）公立医院、{指定医院除外：部队医院...}、{指定医院包含：浙江绿城心血管病医院、深圳地区社区医院...}"
  },
  {
    "事故类型": "疾病、意外",
    "治疗类型": "急诊",
    "医院范围": "医保定点公立医院",
    "发票"："（社保账单：是）"
  },
  ...
]
```

# 输出要求
1. **严格提取信息**：严格按照"字段提取说明"提取信息，不添加任何额外字段。对于未提及的字段，使用默认值。
2. **格式化要求**：输出要求包含一个 JSON 数组，参考示例格式，包含"字段提取说明"的字段。
3. **逻辑校验（必须执行）**：
   - 检查是否有治疗类型（如急诊）在特约中有特殊规定但未单独列出？如有，请立即补录为新情形。
   - 检查基础情形的治疗类型中是否还包含了已拆分出去的类型？如有，请立即删除。
   - 检查医院范围排除项是否完整？
4. **先分析再给出json结果**：在给出最终结果前先进行任务分析，给出你的思考。

# 你的输出""",
    "input_variables": ["base_liability_json", "supplement_text", "structure_tree", "current_liability"],
    "output_format": "JSON数组"
}

# ================== 18. 等待期生成 (extract_general_audit_items) ==================
WAITING_PERIOD_GENERATION_PROMPT = {
    "name": "waiting_period_generation",
    "description": "基于条款文本生成等待期要求",
    "template": """你是一个经验丰富的保险审核专家，请你给出保险责任对应的等待期：

- **保险条款文本段**：
{{waiting_period_text}}
- **保单责任结构表**：
{{structure_tree}}
- **本次处理的保险责任**（计划名称_条款名称_责任名称）：
{{current_liability}}

注意：需要根据保险责任进行匹配，指定之前起保的被保险人不视为新保，抽取新保等待期时忽略。如提到"新保及加保人员重疾、轻症等待期30天"，则"互联网团体重大疾病保险条款"、"互联网附加团体轻度疾病保险条款"等"重疾、轻症"责任的新保等待期为30天。"...新的保险合同的等待期为 0 天"或"新保无等待期"则返回"0天；若保险条款文本段无当前责任相关等待期内容，返回"无等待期"。

输出格式规范：
先对问题进行分析，给出你的思考，同时你的返回应该包含一个JSON对象，格式如下：
```json
{
  "新保等待期": "30天/0天/无等待期"
}
```
---
**请开始根据上述逻辑分析并最终返回JSON结果：**""",
    "input_variables": ["waiting_period_text", "structure_tree", "current_liability"],
    "output_format": "JSON对象，包含新保等待期字段"
}

# ================== 19. 既往症生成 (extract_general_audit_items) ==================
PAST_ILLNESS_GENERATION_PROMPT = {
    "name": "past_illness_generation",
    "description": "基于条款文本生成既往症赔付参数和范围",
    "template": """你是一个经验丰富的保险审核专家，请你给出保险责任对应的既往症赔付参数和严重（重大）既往症范围：
- **保险条款文本段**：
{{past_illness_text}}
- **保单责任结构表**：
{{structure_tree}}
- **本次处理的保险责任**（计划名称_条款名称_责任名称）：
{{current_liability}}

注意：仅需关注本次处理的保险责任，需要根据保险责任进行匹配，如提到"1）疾病身故：承担一般既往症，除外严重既往症。2）重大疾病：参照重大疾病续保的约定。3）意外险/医疗险正常承担所有既往症。"，则"疾病身故"相关责任的既往症为"承担一般既往症但不承担严重既往症"，"重大疾病"相关责任的既往症为"参照重大疾病续保的约定"...。注意同时从文本段中提取严重既往症。必须有上下文原文支持你的结果。无“承担既往症”的正面约定 → 默认返回"承担既往症"。请返回json格式结果。
“不承担严重既往症”默认映射为“承担一般既往症但不承担严重既往症”

你的返回示例：

输出格式规范：
先对问题进行分析，给出你的思考，同时你的返回应该包含一个JSON对象，json示例格式如下：
- 示例1:
```json
{
"本次处理的保险责任": "疾病身故保险金",
"本次处理的保险责任的既往症赔付参数": "承担一般既往症但不承担严重既往症",
"严重既往症范围": "恶性肿瘤、心脏病（心功能不全Ⅱ级（含）以上）、..."
}
```
- 示例2:
```json
{
"本次处理的保险责任": "40种重大疾病保险金",
"本次处理的保险责任的既往症赔付参数": "承担既往症",
"严重既往症范围": ""
}
```
- 示例3:
```json
{
"本次处理的保险责任": "基本医疗保险范围内门急诊医疗费用保险金",
"本次处理的保险责任的既往症赔付参数": "",
"严重既往症范围": ""
}
```

---
**请开始根据上述逻辑分析并最终返回JSON结果：**""",
    "input_variables": ["past_illness_text", "structure_tree", "current_liability"],
    "output_format": "JSON对象，包含既往症相关字段"
}


# ================== 20. 因子抽取 ==================
FEE_SCOPE_FACTOR_EXTRACTION_PROMPT = {
    "name": "fee_scope_factor_extraction",
    "description": "从保险条款中提取理算因子相关内容",
    "template": """你是一个专业的医疗保险理赔系统理算因子配置助手。
现在你将接收一段包含因子上下文的条款文档选段召回结果，你的任务是：针对{{factor_type}}，从条款文本中拆解并提取与该因子相关的内容，生成结构化结果。
---
任务要求：

    1.	责任匹配判断：  
	先判断条款选段是否属于‘{{structure_tree_leaf}}’。若责任不符，返回空列表[]，不再进行后续抽取。若无法判断，则继续进行抽取。
	2.	因子值拆解：
    从条款内容中识别并抽取{{factor_type}}对应的"因子值"。
		-	因子值可以是枚举值、区间值、金额、文字描述、布尔型条件等。注意最终返回数值类型。
		-	若存在多个候选因子值，请结合上下文选择和保险责任'{{structure_tree_leaf}}'相关的值，若存在特别约定因子值，仅参考特别约定内容。若没有提到针对某个责任颗粒度的值，请选择最通用的值。
		-	若条款中没有明确的因子值，请返回 "因子值": "未找到"。
	3.	费用范围识别：
    若条款中对费用适用范围（如"仅限住院费用""门急诊费用不包含"等）有明确或隐含描述，请一并识别。
		-	费用范围需从{{factor_type_to_fee_scope_mapping}}中选择一个最符合的选项。
		-	若文本中有多种费用范围描述，应该都拆出来按照费用范围不同输出多个字典。
		-	若条款中没有明确的费用范围，请返回费用范围的缺省值，{{factor_type}}的缺省值为{{factor_type_to_fee_scope_default}}。
	4.	额外描述提取：
    若条款中包含与费用适用、限制、例外、分摊、年度上限等相关的额外条件，也应一并提取出来，标明其与因子关系。
---
条款文档选段（包含上下文）：
{{retrieved_snippets}}
---
本次需要抽取的因子信息：

	-	因子类型
		{{factor_type}}
	-	因子含义
        {{factor_explanation}}
	-	所属保险责任
	    {{structure_tree_leaf}}
	-	因子类型对应的费用范围枚举与人工说明
		{{fee_scope_descriptions}}
    -	需匹配的责任
        {{structure_tree_leaf}}
---
注意事项：
务必先核对险种名称、责任名称及适用人群；任何一项不符即视为不匹配，立即返回空字典，禁止继续抽取。
保持专业、遵循原文，不自行推测未出现的条款内容。
---
输出格式（示例）：

示例1:
[
{
  "因子类型": "{{factor_type}}",
  "因子值": "需从文本抽取",
  "费用范围": "限定1",
  "额外描述": [
    "额外描述 1",
    "额外描述 2"
  ]
},
{
  "因子类型": "赔付比例",
  "因子值": "需从文本抽取",
  "费用范围": "限定2",
  "额外描述": [
    "额外描述 1",
    "额外描述 2"
  ]
}
]

示例2:
[
{
  "因子类型": "赔付比例",
  "因子值": "需从文本抽取",
  "费用范围": "限定1",
  "额外描述": [
    "额外描述 1",
    "额外描述 2"
  ]
}
]
---
你的输出：
""",
    "input_variables": ["retrieved_snippets", "factor_type", "structure_tree_leaf", "fee_scope_descriptions","factor_type_to_fee_scope_mapping","factor_explanation", "factor_type_to_fee_scope_default"],
    "output_format": "JSON格式的因子提取结果"
}

# ================== 21. 理算因子信息检查 (hybrid_retrieval) ==================
FEE_SCOPE_INFO_CHECK_PROMPT = {
    "name": "fee_scope_info_check",
    "description": "判断保险条款是否包含理算因子相关数值",
    "template": """你是一个理赔专家，现在要判断一段保险条款的原始文本是否在包含理算因子'{{field_name}}'相关数值。例如：数额、比例等。文本中没有显式给出具体数值则认为不包含；如果文本中显式给出了具体数值，则认为包含。
原文如下：
{{base_text}}
请先输出思考过程，用<thought>包裹，然后输出包含/不包含，用<output>包裹。""",
    "input_variables": ["field_name", "base_text"],
    "output_format": "思考过程 + <output>包含/不包含</output>"
}

# ================== 21.1 理算因子信息批量检查 (hybrid_retrieval) ==================
FEE_SCOPE_INFO_CHECK_BATCH_PROMPT = {
    "name": "fee_scope_info_check_batch",
    "description": "批量判断多段保险条款是否包含理算因子相关数值",
    "template": """你是一个理赔专家，现在要判断多段保险条款的原始文本是否包含理算因子'{{field_name}}'相关数值。例如：数额、比例等。文本中没有显式给出具体数值则认为不包含；如果文本中显式给出了具体数值，则认为包含。

请对以下每一段文本分别判断：
{{batch_texts}}

请对每一段文本分别输出判断结果，格式如下：
<result>
<segment_1>
<thought>思考过程</thought>
<output>包含/不包含</output>
</segment_1>
<segment_2>
<thought>思考过程</thought>
<output>包含/不包含</output>
</segment_2>
...（以此类推）
</result>""",
    "input_variables": ["field_name", "batch_texts"],
    "output_format": "批量结果 + <result>包含每段的判断</result>"
}

# ================== 22. 等待期信息检查 (hybrid_retrieval) ==================
WAITING_PERIOD_INFO_CHECK_PROMPT = {
    "name": "waiting_period_info_check",
    "description": "判断保险条款是否包含等待期相关数值",
    "template": """你是一个理赔专家，现在要判断一段保险条款的原始文本是否在包含等待期相关数值。文本中没有显式给出等待期天数则认为不包含；如果文本中显式给出了具体等待期天数，则认为包含。
原文如下：
{{base_text}}
请先输出思考过程，用<thought>包裹，然后输出包含/不包含，用<output>包裹。""",
    "input_variables": ["base_text"],
    "output_format": "思考过程 + <output>包含/不包含</output>"
}

# ================== 23. 特别约定检查 (hybrid_retrieval) ==================
AGREEMENT_CHECK_PROMPT = {
    "name": "agreement_check",
    "description": "判断保险条款是否包含特别约定相关内容",
    "template": """你是一个理赔专家，现在要判断一段保险条款的原始文本是否在包含特别约定'{{field_name}}'相关内容。例如：免责条款、附加条款等。文本中没有显式给出具体特别约定内容，如仅提及特别约定，则认为不包含；如果文本中显式给出了具体特别约定内容，则认为包含。
原文如下：
{{base_text}}
请先输出思考过程，用<thought>包裹，然后输出包含/不包含，用<output>包裹。""",
    "input_variables": ["field_name", "base_text"],
    "output_format": "<thought>思考过程</thought> + <output>包含/不包含</output>"
}

RESPONSIBILITY_DISCERN_CHECK_PROMPT = {
    "name": "responsibility_discern_check",
    "description": "判断保险条款是否包含责任免除相关内容",
    "template": """你是一个理赔专家，现在要判断一段保险条款的原始文本是否包含责任免除'{{field_name}}'相关内容。例如：明确的责任免除条款、除外责任说明等。需要仔细分析文本中是否明确描述了哪些情况不属于保险责任范围。
原文如下：
{{base_text}}
请先输出思考过程，用<thought>包裹，然后输出包含/不包含，用<output>包裹。""",
    "input_variables": ["field_name", "base_text"],
    "output_format": "<thought>思考过程</thought> + <output>包含/不包含</output>"
}

# ================== 24. VLM表格合并提示词 (VLM_markdown_post_processing) ==================
VLM_MERGED_TABLE_PROMPT = {
    "name": "vlm_merged_table_conversion",
    "description": "使用VLM将多个被分页的表格图片合并为单个HTML表格",
    "template": """请将以上多个表格图片合并为一个完整的HTML表格：
    1. 保持表格结构和数据的完整性
    2. 确保表头对齐和列宽一致
    3. 处理跨页的行合并
    4. 仅输出HTML表格代码，不包含任何说明文字
    5. 丢弃涉及职业分类、从业信息等的分类目录、含有证件号码、纳税人识别号等大批量数据的图片表格不转换，其余内容正常转换""",
    "input_variables": [],
    "output_format": "HTML表格代码"
}

# ================== 25. VLM单表格转换提示词 (VLM_markdown_post_processing) ==================
VLM_SINGLE_TABLE_PROMPT = {
    "name": "vlm_single_table_conversion",
    "description": "使用VLM将单个表格图片转换为HTML表格",
    "template": """将图片中的内容准确转换为HTML表格，仅输出表格本身，不包含任何解释、说明或 HTML 代码块标记：
    1. 保持表格结构和数据的完整性
    2. 确保表头对齐和列宽一致
    3. 仅输出HTML表格代码，不包含任何说明文字
    4. 丢弃涉及职业分类、从业信息等的分类目录、含有证件号码、纳税人识别号等大批量数据的图片表格不转换，其余内容正常转换""",
    "input_variables": [],
    "output_format": "HTML表格代码"
}

# ================== 26. 目录生成 (catalog_generator) ==================
CATALOG_GENERATOR_PROMPT = {
    "name": "catalog_generator",
    "description": "基于目录与索引信息生成分层目录结构",
    "template": """**待整理的目录与索引信息：**
```
{{header_md_text}}
```

**任务要求：**
你是一名专业的文档结构分析专家。请基于上述“目录条目-段落索引”列表，重构并生成**严格保序、层级正确**的Markdown目录。

输入数据说明：
1. **数据包含重叠：** 列表前20个条目可能包含上一批次已修复层级的**历史结果**（用于上下文衔接）。
2. **待处理特征：** 所有**未经过处理**的新条目，在输入中均统一表现为一级标题格式（即以 `#` 开头），你需要根据逻辑将其下沉到正确的层级（如 `##` 或 `###`）。

请严格遵循以下执行步骤与规则：

#### 1. 上下文状态识别与锚定（关键步骤）
在开始推断前，优先分析列表**前20个条目**的层级分布特征，判断当前处理状态：
-   **情形A：首批数据（无历史参考）**
    -   **特征**：前20个条目均为一级标题（`#`），且无明显的层级缩进结构。
    -   **操作**：视为文档起始，**不进行上下文继承**。直接依据语义和编号逻辑，从第一行开始构建层级体系。
-   **情形B：滚动衔接（有历史参考）**
    -   **特征**：前20个条目中包含二级（`##`）或更深层级标题。
    -   **操作**：
        1.  **锁定锚点**：前20个已有深层级的条目为“已确定的历史事实”，**禁止修改**其层级和内容，仅用于校准。
        2.  **状态继承**：以第20个条目的层级为基准，推断第21个及后续新条目的层级。例如，若第20项是 `###`，且第21项（原始为`#`）逻辑上是其子项，则应修正为 `####` 或同级 `###`。

#### 2. 层级推断核心规则
针对判定为“待处理”的条目（即看起来是 `#` 但实际需调整的条目）：
-   **语义与编号**：依据标题语义、数字编号（如 `1`, `1.1`, `(1)`, `第一条`）推断层级。
    -   *示例：* `1. 总则` 为一级，`1.1 目的` 应修正为二级。
-   **连续性原则**：同级标题必须保持逻辑连贯，禁止跨层级跳跃（如不应从 `#` 直接跳到 `###`，除非逻辑上缺失中间层）。
-   **格式清洗**：移除输入中伪一级标题的 `#` 符号，替换为正确的Markdown标题标记（`#`, `##`, `###`...）。

#### 3. 特殊标记强制规则
-   **文件分隔符**：遇到 `FILE:` 标记，**必须**保留为一级标题 `# FILE:`。且 `FILE:` 下方的第一条内容默认为该文件的起始一级标题。
-   **特定条款标题**：遇到类似 `XX保险股份有限公司 XX保险条款（X版X款） 注册号：...` 的长标题，**强制**置为一级标题 `#`。

#### 4. 完整性与输出规范
-   **严禁删改**：禁止删除任何索引行，禁止合并、拆分条目，禁止修改标题文本；仅允许标题符号修复。即使某行看起来不像标题，也请保留并仅移除标题符号（转为普通文本），但**必须保留**末尾的 `[数字索引]`。
-   **严格保序**：输出顺序必须与输入列表完全一致。必须保证同一标题文本在输入输出中对应的数字索引不变。
-   **纯净输出**：仅返回整理后的Markdown内容，**不要**包含“根据分析...”、“这是整理后的...”等任何解释性文字或代码块标记（```）。

**目标：生成一份无缝衔接、层级精准的Markdown目录。**""",
    "input_variables": ["header_md_text"],
    "output_format": "Markdown格式的分层目录"
}

# ================== 27. HTML表格转Markdown (html_table_to_markdown) ==================
HTML_TABLE_TO_MARKDOWN_PROMPT = {
    "name": "html_table_to_markdown",
    "description": "将HTML表格转换为Markdown格式的连贯文本段落",
    "template": """请认真阅读并理解下方的表格内容（以HTML形式给出），将其转换为连贯的纯文本段落，并使用 Markdown 一级标题（例如 # 标题）在段落前生成对应主题。

要求如下：
    1. 保留表格的层级信息，将同一行中的所有单元格内容进行**合并或并列表述**，视为同一组信息。
    2. **不要**建立单元格之间的“对应”或“映射”关系（例如不要说“A对应B”），而是直接将它们作为并列的实体名称罗列出来。
    3. 输出结果应为连贯的段落文本，**不要**使用表格、列表格式，也**不要**为每一行生成单独的标题。
    4. **严禁**对原文本作任何归纳总结，只需将表格中的文字原样转化为流畅的语句。
    5. 文本应自然流畅，段落之间可以存在上下文联系，不需要每一行都独立于上下文。

请以此提示为模板，将下面的表格替换为格式化文本并直接输出最终段落结果，不要输出其他解释性文本或操作步骤。
---
表格内容：
{{table_text}}""",
    "input_variables": ["table_text"],
    "output_format": "Markdown格式的连贯文本段落"
}

# ================== 28. 责免信息提取 (responsibility_agent) ==================
RESPONSIBILITY_EXTRACTION_PROMPT = {
    "name": "responsibility_extraction",
    "description": "从责免文本中抽取责免信息实体",
    "template": """# 责免文本
{{text}}

# 输出示例
["保健", "预防", "醉酒", "毒品", "康复", "产后恢复", "拔罐", "轮椅", "眼镜", "隐形眼镜", "配镜", "假眼", "假肢", "助听器", "遗传性疾病", "先天性畸形", "染色体异常", "残疾", "宫外孕", "药物过敏", "整容手术", "美容", "人工流产"]

# 任务说明
1.请你根据'责免文本'抽取责免信息实体，将所有和免责情况相关的信息实体全部抽取出来。
2.输出的格式要参考'输出示例'的格式（注意仅参考格式，不要参考内容），用列表的形式进行输出，列表的每一项是一个免责信息实体，不要对输出的内容做任何的解释说明。
3.确保抽取的信息实体涵盖原始文本提到的所有免责情况，不漏掉任何细节。同时，请注意识别可能蕴含在复杂句式中的信息实体。

# 你的输出""",
    "input_variables": ["text"],
    "output_format": "JSON数组格式的责免信息实体列表"
}

# ================== 29. 健康告知提取 (responsibility_agent) ==================
HEALTH_NOTICE_EXTRACTION_PROMPT = {
    "name": "health_notice_extraction",
    "description": "从健告文本中抽取健康告知列表",
    "template": """## 输出示例
[
    {
        "healthNoticeName": "住院或手术",
        "healthNoticeInfo": "被保险人过去半年内因病住院、手术。"
    },
    {
        "healthNoticeName": "重大疾病",
        "healthNoticeInfo": "被保险人目前或曾经患有癌症（含白血病、淋巴瘤）、脑肿瘤、脑中风、心肌梗死、尿毒症、肝硬化。"
    },...
]

## 健告文本
{{text}}

## 任务说明
1.请你根据'健告文本'抽取健康告知列表。
2.列表每一个dict是一条健康告知条款，'healthNoticeName'是其健康告知概述，'healthNoticeInfo'是其健康告知具体内容。
3.未找到任何的健康告知条款，则输出空列表。
4.输出中每个dict只可以有一个'healthNoticeName'字段和一个'healthNoticeInfo'字段。
5.输出的格式要参考'输出示例'的格式（注意仅参考格式，不要参考内容），用json的形式进行输出。输出的内容不要包含任何```json,```python等字样，不要对输出的内容做任何的解释说明。
6.明确列出每个健告的名称和详细信息，务必包含诸如手术、疾病、服用药品等信息。
7.确保抽取的信息涵盖原始文本提到的所有内容，不漏掉任何细节。同时，请注意识别可能蕴含在复杂句式中的条款。
8.注意，原条款中，健告条款有多少条，你就要输出多少条，不要减少条数。

## 你的输出
""",
    "input_variables": ["text"],
    "output_format": "JSON数组格式的健康告知列表"
}

# ================== 30. 保险条款解析置信度评估  ==================
CONFIDENCE_EVALUATION_PROMPT = {
    "name": "confidence_evaluation",
    "description": "保险条款解析置信度评估专家，验证模型拆解结果准确性",
    "template": """# 角色定义 (Role)
你是一个专业的保险条款解析评估助手。请根据以下输入信息、字段定义及校验规则，对模型提取的保险字段结果进行评分和规则匹配校验。

# 输入信息 (Input)

## 1. 上下文信息
*   **保险条款召回片段 (Context)**：
    ```text
    {{recall_context}}
    ```
*   **当前处理责任 (Liability Keyword)**：
    ```text
    {{plan_clause_liability_keyword}}
    ```
*   **模型拆解结果 (Result)**：
    ```json
    {{ai_extraction_result}}
    ```

## 2. 字段定义 (Definition)
*   **医院范围**：
    *   提取允许理赔的医疗机构类型/等级（如“二级或二级以上的医保定点公立医院”）。
    *   若涉及指定医院（包含或除外），必须使用花括号格式 `{指定医院包含：...}` 或 `{指定医院除外：...}` 输出。
    *   具体医院列表需逐一提取，用顿号连接。
*   **发票**：
    *   若涉及医保结算、强制使用医保/社保卡，记录 `(社保账单：是)`。
    *   若涉及统筹支付，记录 `(统筹支付：是)`。
    *   默认为空。
*   **既往症**：
    *   枚举类型（如：承担既往症、承担一般既往症但不承担以下严重既往症...）。
    *   疾病名称必须从条款中动态抽取并拼接在花括号内。
*   **等待期**：
    *   枚举类型（如：等待期30天、等待期0天、无等待期）。

---

# 校验规则 (Rules)

## 一、召回质量通用规则（预判）

| 规则编号 | 规则名称 | 情景描述 | 评分影响 |
| :--- | :--- | :--- | :--- |
| **R_GEN_01** | **严重幻觉风险** | 召回内容为空，或几乎不包含有效条款文本。 | 所有字段评分倾向为 **0分**。 |
| **R_GEN_02** | **信噪比低** | 召回上下文明显冗余、与字段相关性极弱。 | 对所有字段可信度产生负面影响，需结合内容降分。 |

## 二、字段级规则校验表

请严格比对下列规则。**只有完全符合情景描述时，才视为“命中规则”**。

### 1. 医院范围 (Hospital Scope)
| 规则编号 | 情景描述 | 预期结果倾向 |
| :--- | :--- | :--- |
| **HOSP_POS_01** | 召回文本**明确提及**医院等级（如“二级及以上”），且提取结果准确包含该等级并与责任匹配；且无具体指定医院遗漏。 | **10分** (置信) |
| **HOSP_POS_02** | 召回文本**明确列举**具体医院名称（包含或除外），且提取结果完整枚举了这些名称；且医院等级与责任匹配。 | **10分** (置信) |
| **HOSP_NEG_01** | 召回文本包含医院等级描述，但提取结果未体现。 | **0分** (关键信息遗漏) |
| **HOSP_NEG_02** | 召回文本包含具体指定医院，但提取结果未覆盖或不完整。 | **0-4分** (实体遗漏) |

### 2. 发票 (Invoice)
| 规则编号 | 情景描述 | 预期结果倾向 |
| :--- | :--- | :--- |
| **INV_POS_01** | 召回文本出现“医保/社保”等关键词，且提取结果准确包含“社保账单”约束且责任匹配。 | **10分** (置信) |
| **INV_POS_02** | 召回文本出现“统筹支付”等关键词，且提取结果准确包含“统筹支付”约束且责任匹配。 | **10分** (置信) |
| **INV_POS_02** | 召回文本出现“自费”等关键词，且提取结果准确包含“自费”约束且责任匹配。 | **10分** (置信) |
| **INV_POS_03** | 召回文本出现‘未使用医保需扣除一定比例/金额后赔付’或‘未使用医保按xx比例赔付’且责任匹配，该字段保持默认空值。 | **10分** (置信空返回) |
| **INV_POS_04** | 召回文本全文**未出现**上述关键词，且提取结果为空。 | **10分** (置信空返回) |
| **INV_NEG_01** | 召回文本明确出现“医保/社保”结算要求，但提取结果为空。 | **0分** (明显遗漏) |

### 3. 既往症 (Past Illness)
| 规则编号 | 情景描述 | 预期结果倾向 |
| :--- | :--- | :--- |
| **PAST_POS_01** | 召回文本提及“承担一般既往症”、“严重既往症不赔”或有具体疾病列表，且提取结果准确包含对应约束。 | **10分** (置信) |
| **PAST_POS_02** | 召回文本全文**未出现**“既往/既往症”相关描述，且提取结果为“承担既往症”默认值。 | **10分** (合理默认值) |
| **PAST_NEG_01** | 召回文本明确提及既往症限制条款，但提取结果未体现。 | **0分** (关键信息遗漏) |
| **PAST_NEG_02** | 召回文本全文**未出现**“既往/既往症”相关描述，但提取结果为空。 | **10分** (错误空值) |

### 4. 等待期 (Waiting Period)
| 规则编号 | 情景描述 | 预期结果倾向 |
| :--- | :--- | :--- |
| **WAIT_POS_01** | 召回文本明确提及“无等待期”或“等待期N天”，且提取结果准确提取该天数或状态。 | **10分** (置信) |
| **WAIT_POS_02** | 召回文本全文**未提及**等待期，且提取结果为"无等待期"。 | **10分** (合理默认值) |
| **WAIT_NEG_01** | 召回文本提及了等待期（含无等待期），但提取结果为空。 | **0分** (明显错误) |

---

# 评分流程 (Workflow)

对每一个字段，请按以下步骤执行：

1.  **扫描召回内容**：
    *   检查是否存在 **R_GEN** 类通用质量问题。
2.  **规则匹配（严格校验前缀）**：
    *   判断该字段的提取情况是否**精确命中**该字段专属章节下的规则。
    *   **重要约束**：严禁跨字段匹配规则。
        *   **医院范围** 只能匹配 `HOSP_` 开头的规则。
        *   **发票** 只能匹配 `INV_` 开头的规则。
        *   **既往症** 只能匹配 `PAST_` 开头的规则。
        *   **等待期** 只能匹配 `WAIT_` 开头的规则。
    *   若发现符合其他字段的规则描述但不符合本字段规则前缀，视为**未命中**。
3.  **综合打分**：
    *   **10分**：完全一致，无幻觉，无遗漏（**必须**命中本字段专属的 `_POS_` 类规则）。
    *   **0分**：关键信息严重缺失或严重幻觉（**必须**命中本字段专属的 `_NEG_` 类规则 或 `R_GEN_01`）。
    *   **1-9分**：介于两者之间。此时“命中规则”字段填 `"未命中"`。

---

# 输出要求 (Output)

请严格以 **JSON** 形式输出，输出的 JSON 对象必须且只能包含以下四个顶级键：
- "医院范围"
- "发票" 
- "既往症"
- "等待期"

每个键对应的值必须是一个包含 matched_rule、reasoning、score 三个字段的对象：
*   **matched_rule**：
    *   若 `score` 为 **10** 或 **0**，此处**必须**填写对应的规则编号。
    *   **校验要求**：填写的规则编号必须与当前字段类型严格对应（例如：“等待期”字段绝不可出现 `HOSP_` 开头的规则）。如果找不到同前缀的规则，请填 `"未命中"` 并调整分数至 1-9 分。
    *   若 `score` 为 **1-9**，此处填写 `"未命中"`。
*   **reasoning**：简述评分依据及召回内容与结果的对比分析。
*   **score**：必须为整数。

**输出示例：**
```json
{
  "医院范围": {
    "reasoning": "召回文本明确提及'二级以上公立医院'，结果已准确提取。",
    "matched_rule": "HOSP_POS_01",
    "score": 10
  },
  "发票": {
    "reasoning": "召回提及社保结算，结果虽然提取了但缺少统筹支付信息，存在轻微遗漏。",
    "matched_rule": "未命中",
    "score": 6
  },
  "既往症": {
    "reasoning": "文中未提及既往症，结果为空，符合逻辑。",
    "matched_rule": "PRE_POS_02",
    "score": 10
  },
  "等待期": {
    "reasoning": "文中明确提及等待期30天，但结果为空。",
    "matched_rule": "WAIT_NEG_01",
    "score": 0
  }
}
```""",
    "input_variables": ["recall_context", "ai_extraction_result", "plan_clause_liability_keyword"],
    "output_format": "JSON格式，包含score(0-10)和reasoning字段"
}

# ================== 31. 医院范围解析 (code_parsers) ==================
HOSPITAL_SCOPE_PARSING_PROMPT = {
    "name": "hospital_scope_parsing",
    "description": "将复杂医院范围文本解析为结构化的 HospitalScopeDto 列表",
    "template": """你是一名资深的医疗保险数据治理专家。请分析输入的医院范围描述，将其拆解并结构化为符合系统定义的 JSON 对象。

输入文本：
{{hospital_scope_text}}

## 任务执行步骤
1. **语义分析**：识别文本中的逻辑连接词（"、"、"或"、"且"）。如果存在并列的不同类型医院，必须拆分为多个对象。
2. **实体抽取**：提取等级、性质、类别等关键属性。
3. **模糊映射**：根据下方的【模糊概念映射表】处理"县级以上"、"基层"等非标准术语。
4. **格式化**：输出标准 JSON。

## 码值定义（严格遵守）

### 1. 基础字段
- **defDirection**: "1" (包含, 默认), "2" (除外/不含)
- **defLevel**: "1" (按属性配置), "2" (指定医院, 仅当文本明确列出具体医院名时)

### 2. 医院属性 (hospitalParam)
**A. 级别 (hospitalLevels)**
- "0": 无分级
- "1": 一级 (关键词: 一级, 社区, 基层, 卫生院)
- "2": 二级 (关键词: 二级, 县级, 区级)
- "3": 三级 (关键词: 三级, 市级)
*   *特殊规则*: "二级及以上/县级以上" -> "2,3"; "二级以上" -> "3"

**B. 等级 (hospitalGrades)**
- "0": 无等级, "1": 特等, "2": 甲等, "3": 乙等, "4": 丙等

**C. 性质 (hospitalNatures)**
- "01": 合资, "02": 民营/私立, "03": 外资, "04": 公立

**D. 机构类别 (hospitalOrgTypes)**
- "1": 医院 (默认)
- "2": 门诊 (关键词: 门诊部, 卫生所, 医务室, 社区站)
- "6": 疾控, "7": 其他

**E. 医保 (isNssfHospital)**
- "Y": 是 (医保/社保/定点), "N": 否

**F. 医院类别 (hospitalTypes) [多选逗号分隔]**
- "01": 武警/军队, "02": 综合性, "03": 专科性, "06": 康复
- "07": 职工, "08": 校/厂医院, "10": 联合诊所, "11": 专科诊所
- "16": 药店, "17": 医务室, "12": 国际医疗网
- "04": 公立外宾, "05": 公立特需

## 复杂逻辑处理指南

1. **混合条件拆分**：
   - 文本："二级以上医保定点公立医院或社保定点医院"
   - 处理：拆分为2个对象。
     - 对象1: {hospitalLevels="3", hospitalNatures="04", isNssfHospital="Y"}
     - 对象2: {isNssfHospital="Y"}

2. **指定医院解析**：
   - 文本："{指定医院包含：上海瑞金医院、北京协和医院}"
   - 处理：defLevel="2", hospitalNames="上海瑞金医院,北京协和医院" (去除花括号和前缀)

3. **下沉市场术语**：
   - "县（区）级以上" -> 映射为 "二级" 和 "三级" (Levels="2,3")
   - "社区卫生服务中心" -> 映射为 "一级" (Levels="1") 且 "门诊" (OrgTypes="2")

## 输出格式规范

先对问题进行分析，给出你的思考，同时你的返回应该包含一个JSON对象，格式如下：
```json
{
  "hospitalScopes": [
    {
        "defDirection": "1",
        "defLevel": "1",
        "hospitalParam": {
            "hospitalLevels": "2,3",
            "hospitalNatures": "04",
            "hospitalOrgTypes": "1",
            "isNssfHospital": "Y"
        }
    },
    {
        "defDirection":"1",
        "defLevel":"2",
        "hospitalNames":"XX医院,XX社康中心"
    }
  ]
}
```
---
**请开始根据上述逻辑分析并最终返回JSON结果：**"""
}

# ================== 所有prompt的汇总配置 ==================
ALL_PROMPTS = {
    # extract_general_audit_items prompts
    "special_agreement_index": SPECIAL_AGREEMENT_INDEX_PROMPT,
    "responsibility_discern_index": RESPONSIBILITY_INDEX_EXTRACTION_PROMPT,
    "liability_index_extraction": LIABILITY_INDEX_EXTRACTION_PROMPT,
    "waiting_period_extraction": WAITING_PERIOD_EXTRACTION_PROMPT_NEW,
    "past_illness_extraction": PAST_ILLNESS_EXTRACTION_PROMPT_NEW,
    "multi_scenario_extraction": MULTI_SCENARIO_EXTRACTION_PROMPT,
    "base_compensation_generation": BASE_COMPENSATION_GENERATION_PROMPT,
    "multi_compensation_generation": MULTI_COMPENSATION_GENERATION_PROMPT,
    "waiting_period_generation": WAITING_PERIOD_GENERATION_PROMPT,
    "past_illness_generation": PAST_ILLNESS_GENERATION_PROMPT,
    "fee_scope_factor_extraction": FEE_SCOPE_FACTOR_EXTRACTION_PROMPT,
    # hybrid_retrieval prompts
    "fee_scope_info_check": FEE_SCOPE_INFO_CHECK_PROMPT,
    "fee_scope_info_check_batch": FEE_SCOPE_INFO_CHECK_BATCH_PROMPT,
    "waiting_period_info_check": WAITING_PERIOD_INFO_CHECK_PROMPT,
    "agreement_check": AGREEMENT_CHECK_PROMPT,
    "responsibility_discern_check": RESPONSIBILITY_DISCERN_CHECK_PROMPT,
    # VLM_markdown_post_processing prompts
    "vlm_merged_table_conversion": VLM_MERGED_TABLE_PROMPT,
    "vlm_single_table_conversion": VLM_SINGLE_TABLE_PROMPT,
    # catalog_generator prompt
    "catalog_generator": CATALOG_GENERATOR_PROMPT,
    # html_table_to_markdown prompt
    "html_table_to_markdown": HTML_TABLE_TO_MARKDOWN_PROMPT,
    # responsibility_agent prompts
    "responsibility_extraction": RESPONSIBILITY_EXTRACTION_PROMPT,
    "health_notice_extraction": HEALTH_NOTICE_EXTRACTION_PROMPT,
    # confidence_evaluation prompt
    "confidence_evaluation": CONFIDENCE_EVALUATION_PROMPT,
    # hospital_scope_parsing prompt
    "hospital_scope_parsing": HOSPITAL_SCOPE_PARSING_PROMPT
}
# ALL_PROMPTS = {
#     "fee_scope_factor_extraction": FEE_SCOPE_FACTOR_EXTRACTION_PROMPT,
#
# }
# ================== 使用示例 ==================
def get_prompt_config(prompt_name):
    """获取指定prompt的配置"""
    return ALL_PROMPTS.get(prompt_name)

def list_all_prompts():
    """列出所有可用的prompt配置"""
    return list(ALL_PROMPTS.keys())

def upload_all_prompts_to_langfuse():
    """上传所有本地 prompt 到 Langfuse 服务器"""
    import requests

    langfuse_config = _get_langfuse_connection_config()
    BASE_URL = langfuse_config["host"]
    PUBLIC_KEY = langfuse_config["public_key"]
    SECRET_KEY = langfuse_config["secret_key"]

    # 使用当前所有已定义的 Prompts
    prompts = list(ALL_PROMPTS.values())

    print(f"\n开始上传所有 {len(prompts)} 个本地 prompt 到 Langfuse 服务器...\n")

    # 批量创建
    success_count = 0
    failed_count = 0
    failed_prompts = []

    for p in prompts:
        # 先获取服务器上的版本，用于 diff 对比
        server_prompt = None
        try:
            langfuse_client = _create_langfuse_client()
            server_prompt = langfuse_client.get_prompt(p["name"], label="production")
            langfuse_client.flush()
        except Exception:
            # 如果获取失败，说明服务器上没有这个 prompt
            pass

        # 如果有服务器版本，显示 diff
        if server_prompt:
            print(f"\n--- 检查 prompt: {p['name']} ---")
            show_diff_rich(server_prompt.prompt, p["template"], p["name"])

        # 上传/更新 prompt
        payload = {
            "type": "text",
            "name": p["name"],
            "prompt": p["template"],
            "config": {
                "description": p.get("description", ""),
                "input_variables": p.get("input_variables", []),
                "output_format": p.get("output_format", "")
            },
            # 与预期一致：生产 + latest 标签
            "labels": ["production", "latest"],
            "tags": ["insurance"]
        }

        try:
            resp = requests.post(
                f"{BASE_URL}/api/public/v2/prompts",
                auth=(PUBLIC_KEY, SECRET_KEY),
                json=payload,
                timeout=30
            )
        except Exception as e:
            print(f"✗ 网络错误: {p['name']} - {e}")
            failed_count += 1
            failed_prompts.append((p['name'], f"网络错误: {e}"))
            continue

        status = resp.status_code
        # Langfuse 可能返回 201 Created 或 200 OK
        if status in (200, 201):
            try:
                data = resp.json()
                pid = data.get("id") or data.get("name")
                print(f"✓ 上传成功: {p['name']} (id={pid})")
                success_count += 1
            except Exception:
                print(f"✓ 上传成功: {p['name']} (status={status})")
                success_count += 1
        else:
            # 打印更友好的错误信息
            body = None
            try:
                body = resp.text
            except Exception:
                body = str(resp)
            print(f"✗ 上传失败: {p['name']}, HTTP {status}, Error: {body}")
            failed_count += 1
            failed_prompts.append((p['name'], f"HTTP {status}: {body}"))

    # 打印总结
    print("\n" + "=" * 80)
    print("上传完成")
    print("=" * 80)
    print(f"总数: {len(prompts)}")
    print(f"成功: {success_count}")
    print(f"失败: {failed_count}")

    if failed_prompts:
        print("\n失败的 prompt:")
        for name, error in failed_prompts:
            print(f"  ✗ {name}: {error}")

    return success_count, failed_prompts


def upload_single_prompt_to_langfuse(prompt_name):
    """上传单个本地 prompt 到 Langfuse 服务器"""
    import requests

    # 检查 prompt 是否存在
    if prompt_name not in ALL_PROMPTS:
        print(f"✗ 本地不存在该 prompt: {prompt_name}")
        return False

    langfuse_config = _get_langfuse_connection_config()
    BASE_URL = langfuse_config["host"]
    PUBLIC_KEY = langfuse_config["public_key"]
    SECRET_KEY = langfuse_config["secret_key"]

    p = ALL_PROMPTS[prompt_name]

    print(f"\n开始上传单个 prompt: {prompt_name}")

    # 先获取服务器上的版本，用于 diff 对比
    server_prompt = None
    try:
        langfuse_client = _create_langfuse_client()
        server_prompt = langfuse_client.get_prompt(prompt_name, label="production")
        langfuse_client.flush()
        print(f"--- 本地与服务器版本对比 ---")
        show_diff_rich(server_prompt.prompt, p["template"], prompt_name)
    except Exception as e:
        print(f"服务器上未找到该 prompt，将创建新 prompt")

    # 上传/更新 prompt
    payload = {
        "type": "text",
        "name": p["name"],
        "prompt": p["template"],
        "config": {
            "description": p.get("description", ""),
            "input_variables": p.get("input_variables", []),
            "output_format": p.get("output_format", "")
        },
        # 与预期一致：生产 + latest 标签
        "labels": ["production", "latest"],
        "tags": ["insurance"]
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/api/public/v2/prompts",
            auth=(PUBLIC_KEY, SECRET_KEY),
            json=payload,
            timeout=30
        )
    except Exception as e:
        print(f"✗ 网络错误: {e}")
        return False

    status = resp.status_code
    if status in (200, 201):
        try:
            data = resp.json()
            pid = data.get("id") or data.get("name")
            print(f"✓ 上传成功: {p['name']} (id={pid})")
        except Exception:
            print(f"✓ 上传成功: {p['name']} (status={status})")
        return True
    else:
        body = None
        try:
            body = resp.text
        except Exception:
            body = str(resp)
        print(f"✗ 上传失败: {p['name']}, HTTP {status}, Error: {body}")
        return False

from rich.console import Console
from rich.syntax import Syntax
import difflib

def show_diff_rich(old_text, new_text, prompt_name):
    """
    使用 Rich 库显示带有语法高亮的差异
    """
    console = Console()

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    # 生成 unified diff 字符串
    diff_generator = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f'{prompt_name} (本地)',
        tofile=f'{prompt_name} (服务器)',
        lineterm=''
    )

    diff_text = "".join(diff_generator)

    if not diff_text:
        console.print(f"[bold green]✓ {prompt_name} 无差异[/bold green]")
        return

    # 核心：使用 Syntax 渲染 diff 格式
    # theme 可选: "monokai", "github-dark", "solarized-light", "ansi_dark" 等
    syntax = Syntax(diff_text, "diff", theme="monokai", line_numbers=True)

    console.print(f"\n[bold underline]差异对比: {prompt_name}[/bold underline]")
    console.print(syntax)
    console.print("=" * 80)


def _update_prompt_in_source_file(prompt_name: str, new_template: str) -> bool:
    """
    更新源文件中指定 prompt 的 template 字段

    Args:
        prompt_name: prompt 名称（如 'field_relevance'）
        new_template: 新的 template 内容

    Returns:
        bool: 是否成功更新
    """
    import re
    import os

    # 获取当前文件路径
    current_file = os.path.abspath(__file__)

    try:
        with open(current_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 构建匹配 prompt 定义块的正则表达式
        # 查找类似 "name": "field_relevance" 的位置，然后匹配整个字典中的 template 字段
        # 模式：匹配 "name": "prompt_name" 所在的字典块中的 "template": """...""" 部分

        # 首先找到包含该 prompt_name 的字典定义
        # 匹配格式: _PROMPT = { ... "name": "xxx", ... "template": """...""", ... }

        # 使用更精确的方式：找到 "name": "prompt_name" 然后向前向后扩展找到完整的字典块

        # 方法：分段查找和替换
        # 1. 找到 "name": "prompt_name" 的位置
        name_pattern = rf'"name"\s*:\s*"{re.escape(prompt_name)}"'
        name_match = re.search(name_pattern, content)

        if not name_match:
            print(f"  ⚠ 在源文件中找不到 prompt: {prompt_name}")
            return False

        name_pos = name_match.start()

        # 2. 向前找到字典开始的位置（找到 = { 或变量名）
        # 向后找到 template 字段

        # 在 name 位置之后找到 template 字段
        # template 可能使用 """ 或 ''' 或普通引号
        # 需要找到从 name_pos 开始向后的 "template": """..."""

        # 获取从 name_pos 开始的后续内容，但限制范围避免匹配到其他 prompt 的 template
        # 假设每个 prompt 定义之间有换行和注释分隔，我们在下一个 _PROMPT = 之前查找

        # 找到下一个 PROMPT 定义的位置作为搜索边界
        next_prompt_pattern = r'\n[A-Z_]+_PROMPT(?:_NEW)?\s*=\s*{'
        next_match = re.search(next_prompt_pattern, content[name_pos:])
        if next_match:
            search_end = name_pos + next_match.start()
        else:
            search_end = len(content)

        search_content = content[name_pos:search_end]

        # 在这个范围内找到 "template": 字段
        # 支持 """ 和 ''' 两种多行字符串
        template_pattern = r'("template"\s*:\s*)("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"\\]*(?:\\.[^"\\]*)*")'
        template_match = re.search(template_pattern, search_content)

        if not template_match:
            print(f"  ⚠ 在 {prompt_name} 中找不到 template 字段")
            return False

        # 计算实际位置
        template_start = name_pos + template_match.start()
        template_end = name_pos + template_match.end()

        # 构建新的 template 字段
        # 使用三引号格式
        new_template_field = f'"template": """{new_template}"""'

        # 替换内容
        new_content = content[:template_start] + new_template_field + content[template_end:]

        # 写回文件
        with open(current_file, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return True

    except Exception as e:
        print(f"  ✗ 更新源文件失败: {str(e)}")
        return False


def export_prompts_from_langfuse():
    """从 Langfuse 服务器导出所有 prompt 并覆盖本地配置（包括源文件）"""
    # 初始化 Langfuse 客户端
    langfuse = _create_langfuse_client()

    exported_count = 0
    failed_exports = []
    updated_prompts = []
    no_change_prompts = []
    file_updated_prompts = []

    # 获取所有本地 prompt 名称
    local_prompt_names = list(ALL_PROMPTS.keys())

    print(f"开始从 Langfuse 导出 {len(local_prompt_names)} 个 prompt...\n")

    for prompt_name in local_prompt_names:
        try:
            # 从 Langfuse 获取 prompt
            prompt = langfuse.get_prompt(prompt_name, label="production")

            # 更新本地配置
            if prompt_name in ALL_PROMPTS:
                old_template = ALL_PROMPTS[prompt_name]["template"]
                new_template = prompt.prompt

                # 检查是否有差异
                if old_template != new_template:
                    # 显示差异
                    show_diff_rich(old_template, new_template, prompt_name)
                    updated_prompts.append(prompt_name)

                    # 更新源文件
                    if _update_prompt_in_source_file(prompt_name, new_template):
                        file_updated_prompts.append(prompt_name)
                        print(f"  ✓ 已更新源文件中的 {prompt_name}")
                else:
                    no_change_prompts.append(prompt_name)

                # 更新内存中的配置
                ALL_PROMPTS[prompt_name]["template"] = new_template
                ALL_PROMPTS[prompt_name]["description"] = prompt.config.get("description", "")
                ALL_PROMPTS[prompt_name]["input_variables"] = prompt.config.get("input_variables", [])
                ALL_PROMPTS[prompt_name]["output_format"] = prompt.config.get("output_format", "")

                exported_count += 1
            else:
                failed_exports.append((prompt_name, "本地不存在该 prompt"))

        except Exception as e:
            failed_exports.append((prompt_name, str(e)))

    # 关闭 Langfuse 客户端
    langfuse.flush()

    # 打印总结
    print("\n" + "=" * 80)
    print("导出完成")
    print("=" * 80)
    print(f"成功处理: {exported_count} 个")
    if updated_prompts:
        print(f"有更新的: {len(updated_prompts)} 个")
        for name in updated_prompts:
            print(f"  ✓ {name}")
    if file_updated_prompts:
        print(f"已写入源文件: {len(file_updated_prompts)} 个")
        for name in file_updated_prompts:
            print(f"  📝 {name}")
    if no_change_prompts:
        print(f"无变化的: {len(no_change_prompts)} 个")
        for name in no_change_prompts:
            print(f"  - {name}")
    if failed_exports:
        print(f"失败的: {len(failed_exports)} 个")
        for name, error in failed_exports:
            print(f"  ✗ {name}: {error}")

    return exported_count, failed_exports, updated_prompts, no_change_prompts


def export_single_prompt_from_langfuse(prompt_name):
    """从 Langfuse 导出单个 prompt（包括更新源文件）"""
    langfuse = _create_langfuse_client()

    try:
        prompt = langfuse.get_prompt(prompt_name, label="production")

        if prompt_name in ALL_PROMPTS:
            old_template = ALL_PROMPTS[prompt_name]["template"]
            new_template = prompt.prompt

            # 显示差异
            if old_template != new_template:
                show_diff_rich(old_template, new_template, prompt_name)

                # 更新源文件
                if _update_prompt_in_source_file(prompt_name, new_template):
                    print(f"  ✓ 已更新源文件中的 {prompt_name}")
                else:
                    print(f"  ⚠ 源文件更新失败，仅更新内存中的配置")
            else:
                print(f"\n{prompt_name}: 无变化")

            # 更新内存中的配置
            ALL_PROMPTS[prompt_name]["template"] = new_template
            ALL_PROMPTS[prompt_name]["description"] = prompt.config.get("description", "")
            ALL_PROMPTS[prompt_name]["input_variables"] = prompt.config.get("input_variables", [])
            ALL_PROMPTS[prompt_name]["output_format"] = prompt.config.get("output_format", "")

            print(f"\n✓ 成功导出并更新 prompt: {prompt_name}")
            return True
        else:
            print(f"✗ 本地不存在该 prompt: {prompt_name}")
            return False

    except Exception as e:
        print(f"✗ 导出失败: {prompt_name} (错误: {str(e)})")
        return False

    finally:
        langfuse.flush()


def interactive_menu():
    """使用 prompt_toolkit 实现交互式菜单"""
    try:
        from prompt_toolkit import prompt
        from prompt_toolkit.shortcuts import radiolist_dialog
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.completion import WordCompleter
    except ImportError:
        print("错误: 需要安装 prompt_toolkit 库")
        print("请运行: pip install prompt_toolkit")
        return None

    # 主菜单选项
    options = [
        ("1", "上传所有本地 prompt 到服务器"),
        ("2", "上传指定本地 prompt 到服务器"),
        ("3", "从服务器导出所有 prompt"),
        ("4", "从服务器导出指定 prompt"),
        ("5", "列出所有本地 prompt"),
        ("6", "退出")
    ]

    result = radiolist_dialog(
        title="Prompt 配置管理工具",
        text="请选择操作:",
        values=options,
    ).run()

    return result


def select_prompt_interactively():
    """交互式选择 prompt"""
    try:
        from prompt_toolkit.shortcuts import radiolist_dialog
    except ImportError:
        print("错误: 需要安装 prompt_toolkit 库")
        return None

    # 获取所有 prompt 列表
    prompts = list_all_prompts()
    prompt_options = [(name, f"{name} - {ALL_PROMPTS[name]['description'][:50]}...") for name in prompts]

    result = radiolist_dialog(
        title="选择 Prompt",
        text="请选择要导出的 prompt:",
        values=prompt_options,
    ).run()

    return result


def is_terminal():
    """检测是否在终端中运行"""
    import sys
    return sys.stdin.isatty()


def simple_menu():
    """简单的数字菜单（用于非终端环境）"""
    print("=" * 80)
    print("Prompt 配置管理工具")
    print("=" * 80)
    print("\n请选择操作:")
    print("1. 上传所有本地 prompt 到服务器")
    print("2. 上传指定本地 prompt 到服务器")
    print("3. 从服务器导出所有 prompt")
    print("4. 从服务器导出指定 prompt")
    print("5. 列出所有本地 prompt")
    print("6. 退出")
    print("-" * 80)

    try:
        choice = input("\n请输入选项数字 (1-6): ").strip()
        return choice
    except KeyboardInterrupt:
        print("\n\n操作已取消")
        exit(0)
    except EOFError:
        print("\n\n输入已关闭")
        exit(0)


if __name__ == "__main__":
    # 检查是否在终端中
    if not is_terminal():
        # 非终端环境，直接使用简单的数字输入
        choice = simple_menu()
    else:
        # 终端环境，尝试使用交互式菜单
        try:
            from prompt_toolkit.shortcuts import radiolist_dialog
            has_interactive = True
        except ImportError:
            has_interactive = False

        if not has_interactive:
            print("提示: 可以安装 prompt_toolkit 获得更好的交互体验")
            print("运行: pip install prompt_toolkit\n")

            # 没有 prompt_toolkit，使用简单菜单
            choice = simple_menu()
        else:
            # 有 prompt_toolkit，使用交互式菜单
            try:
                choice = interactive_menu()
                if choice is None:
                    print("\n菜单初始化失败")
                    exit(1)
            except KeyboardInterrupt:
                print("\n\n操作已取消")
                exit(0)

    # 处理用户选择
    if choice == "1":
        print("\n开始上传所有本地 prompt 到 Langfuse 服务器...")
        upload_all_prompts_to_langfuse()
    elif choice == "2":
        if is_terminal() and has_interactive:
            prompt_name = select_prompt_interactively()
            if prompt_name is None:
                print("\n未选择 prompt")
                exit(1)
        else:
            prompt_name = input("\n请输入要上传的 prompt 名称: ").strip()

        if prompt_name:
            print(f"\n开始上传单个 prompt: {prompt_name}")
            upload_single_prompt_to_langfuse(prompt_name)
        else:
            print("错误: prompt 名称不能为空")
    elif choice == "3":
        print("\n开始从 Langfuse 导出所有 prompt 到本地...")
        export_prompts_from_langfuse()
    elif choice == "4":
        if is_terminal() and has_interactive:
            prompt_name = select_prompt_interactively()
            if prompt_name is None:
                print("\n未选择 prompt")
                exit(1)
        else:
            prompt_name = input("\n请输入要导出的 prompt 名称: ").strip()

        if prompt_name:
            print(f"\n开始从 Langfuse 导出 prompt: {prompt_name}")
            export_single_prompt_from_langfuse(prompt_name)
        else:
            print("错误: prompt 名称不能为空")
    elif choice == "5":
        print("\n本地可用的 prompt 配置:")
        print("=" * 80)
        for name in list_all_prompts():
            config = get_prompt_config(name)
            print(f"\n{name}:")
            print(f"  描述: {config['description']}")
    elif choice == "6" or choice is None:
        print("\n已退出")
    else:
        print(f"\n错误: 无效的选项 '{choice}'")
