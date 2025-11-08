"""
AI 流程图生成器 - 后端 API
支持调用大模型生成 draw.io XML 格式的流程图
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, List, Tuple
import httpx
import json
import os
import re
from datetime import datetime
from xml.etree import ElementTree as ET
from dotenv import load_dotenv
import asyncio

# 加载环境变量（优先加载 .env 文件）
load_dotenv()

# 使用国内 CDN 镜像
app = FastAPI(
    title="AI 流程图生成器",
    docs_url=None,  # 禁用默认的 docs
    redoc_url=None  # 禁用默认的 redoc
)

# 配置 CORS（允许前端跨域请求）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应该限制具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 数据模型 ==========

class Message(BaseModel):
    role: str  # "user" 或 "assistant"
    content: str

class AIConfigModel(BaseModel):
    """AI API 配置模型"""
    id: Optional[str] = None  # 配置 ID（自动生成）
    name: str  # API 名称
    base_url: str  # API 基础 URL
    api_key: str  # API 密钥
    model: str  # 模型名称
    enabled: bool = True  # 是否启用
    priority: int = 0  # 优先级（数字越小优先级越高）

class AIConfigCreateRequest(BaseModel):
    """创建 AI 配置请求"""
    name: str
    base_url: str
    api_key: str
    model: str
    enabled: bool = True
    priority: int = 0

class AIConfigUpdateRequest(BaseModel):
    """更新 AI 配置请求"""
    name: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    enabled: Optional[bool] = None
    priority: Optional[int] = None

class DiagramGenerateRequest(BaseModel):
    prompt: str
    messages: Optional[List[Message]] = []  # 对话历史
    skip_apis: Optional[List[str]] = []     # 要跳过的 API 名称列表

class DiagramSaveRequest(BaseModel):
    xml: str
    name: Optional[str] = None

class DiagramResponse(BaseModel):
    id: str
    xml: str
    name: Optional[str] = None
    created_at: str

# ========== 模拟数据库 ==========
# 生产环境应使用 PostgreSQL/MongoDB
diagrams_db: Dict[str, dict] = {}
diagram_counter = 1

# ========== AI 配置管理器 ==========
class AIConfigManager:
    """
    AI 配置管理器（单例模式）
    负责管理所有 AI API 配置的增删改查
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.configs: Dict[str, AIConfigModel] = {}
        self.config_counter = 1
        self._initialized = True

        # 初始化默认配置
        self._load_default_configs()

    def _load_default_configs(self):
        """
        加载默认配置
        从环境变量（.env文件）读取配置
        """
        # 尝试从环境变量加载默认配置
        config_from_env = self._load_config_from_env()

        if config_from_env:
            # 如果环境变量中有配置，使用环境变量的配置
            print(f"[配置管理器] 从环境变量加载了默认配置: {config_from_env['name']}")
            config_id = str(self.config_counter)
            self.config_counter += 1

            config = AIConfigModel(
                id=config_id,
                **config_from_env
            )
            self.configs[config_id] = config
        else:
            # 如果没有环境变量配置，提示用户配置
            print("=" * 60)
            print("[配置管理器] ⚠️  未找到 .env 配置文件")
            print("[配置管理器] 请按照以下步骤配置:")
            print("[配置管理器] 1. 复制 backend/.env.example 为 backend/.env")
            print("[配置管理器] 2. 在 .env 文件中填写你的 API 配置")
            print("[配置管理器] 3. 重启服务")
            print("[配置管理器] 或者，你也可以在前端界面中手动添加 AI 配置")
            print("=" * 60)

        print(f"[配置管理器] 已加载 {len(self.configs)} 个配置")

    def _load_config_from_env(self) -> Optional[dict]:
        """
        从环境变量加载单个默认配置
        读取 DEFAULT_AI_* 环境变量
        返回配置字典，如果没有找到配置则返回 None
        """
        name = os.getenv("DEFAULT_AI_NAME")
        base_url = os.getenv("DEFAULT_AI_BASE_URL")
        api_key = os.getenv("DEFAULT_AI_API_KEY")
        model = os.getenv("DEFAULT_AI_MODEL")

        # 检查必需字段
        if not all([name, base_url, api_key, model]):
            print("[配置管理器] 环境变量中的默认配置不完整")
            return None

        # 返回配置（固定 enabled=True, priority=0）
        return {
            "name": name,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "enabled": True,
            "priority": 0
        }

    def get_all_configs(self) -> List[AIConfigModel]:
        """获取所有配置（按优先级排序）"""
        configs = list(self.configs.values())
        configs.sort(key=lambda x: x.priority)
        return configs

    def get_enabled_configs(self) -> List[AIConfigModel]:
        """获取所有启用的配置（按优先级排序）"""
        configs = [c for c in self.configs.values() if c.enabled]
        configs.sort(key=lambda x: x.priority)
        return configs

    def get_config(self, config_id: str) -> Optional[AIConfigModel]:
        """获取指定配置"""
        return self.configs.get(config_id)

    def create_config(self, config_data: AIConfigCreateRequest) -> AIConfigModel:
        """创建新配置"""
        config_id = str(self.config_counter)
        self.config_counter += 1

        config = AIConfigModel(
            id=config_id,
            **config_data.dict()
        )
        self.configs[config_id] = config

        print(f"[配置管理器] 创建配置: {config.name} (ID: {config_id})")
        return config

    def update_config(self, config_id: str, update_data: AIConfigUpdateRequest) -> Optional[AIConfigModel]:
        """更新配置"""
        if config_id not in self.configs:
            return None

        config = self.configs[config_id]
        update_dict = update_data.dict(exclude_unset=True)

        for key, value in update_dict.items():
            setattr(config, key, value)

        print(f"[配置管理器] 更新配置: {config.name} (ID: {config_id})")
        return config

    def delete_config(self, config_id: str) -> bool:
        """删除配置"""
        if config_id not in self.configs:
            return False

        config = self.configs[config_id]
        del self.configs[config_id]

        print(f"[配置管理器] 删除配置: {config.name} (ID: {config_id})")
        return True

    def get_configs_as_dict_list(self) -> List[dict]:
        """
        获取配置列表（字典格式，兼容原有代码）
        只返回启用的配置
        """
        enabled_configs = self.get_enabled_configs()
        return [
            {
                "name": c.name,
                "base_url": c.base_url,
                "api_key": c.api_key,
                "model": c.model
            }
            for c in enabled_configs
        ]

# 创建全局配置管理器实例
config_manager = AIConfigManager()

# ========== AI 模型配置（多 API 故障转移）==========
# 注意: 配置已迁移到 AIConfigManager，AI_APIS 保留用于向后兼容
# 实际使用的配置通过 config_manager.get_configs_as_dict_list() 获取
def get_ai_apis():
    """获取 AI API 配置列表（动态从配置管理器获取）"""
    return config_manager.get_configs_as_dict_list()

# Draw.io XML 生成的系统提示词（简化版）
SYSTEM_PROMPT = """你是 draw.io 流程图生成助手。根据用户描述生成 XML 格式的流程图。

重要规则：
1. 每个属性只能出现一次（如 x, y, width, height）
2. 不要重复定义相同的属性
3. id 必须唯一
4. 只返回 XML，不要解释

XML 格式：
<mxfile host="app.diagrams.net">
  <diagram name="Page-1" id="d1">
    <mxGraphModel>
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <!-- 矩形 -->
        <mxCell id="2" value="文本" style="rounded=1;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="100" y="100" width="120" height="60" as="geometry"/>
        </mxCell>
        <!-- 菱形 -->
        <mxCell id="3" value="判断?" style="rhombus;whiteSpace=wrap;html=1;" vertex="1" parent="1">
          <mxGeometry x="80" y="200" width="160" height="80" as="geometry"/>
        </mxCell>
        <!-- 连线 -->
        <mxCell id="4" style="edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;" edge="1" parent="1" source="2" target="3">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>

只返回 XML，不要解释。"""

# ========== XML 验证和修复工具 ==========

def validate_xml_strict(xml_string: str) -> Tuple[bool, str]:
    """
    严格验证 XML 是否为有效的 draw.io 格式
    返回: (是否有效, 错误信息)
    """
    try:
        # 1. 基本检查：不能为空
        if not xml_string or len(xml_string.strip()) == 0:
            return False, "XML 内容为空"

        # 2. 清理 markdown 标记
        cleaned = xml_string.replace('```xml', '').replace('```', '').strip()

        # 3. 必须包含 mxfile 或 mxGraphModel
        if '<mxfile' not in cleaned and '<mxGraphModel' not in cleaned:
            return False, "缺少 mxfile 或 mxGraphModel 标签"

        # 4. 使用 ElementTree 验证 XML 语法
        try:
            root = ET.fromstring(cleaned)
        except ET.ParseError as e:
            return False, f"XML 语法错误: {str(e)}"

        # 5. 检查必要的结构
        # 如果是 mxfile 格式，必须包含 diagram
        if root.tag == 'mxfile':
            diagrams = root.findall('.//diagram')
            if not diagrams:
                return False, "mxfile 中缺少 diagram 元素"

            # 检查是否有 mxGraphModel
            has_model = False
            for diagram in diagrams:
                if diagram.find('.//mxGraphModel') is not None:
                    has_model = True
                    break
            if not has_model:
                return False, "diagram 中缺少 mxGraphModel"

        # 6. 检查是否有实际的图形元素 (mxCell)
        cells = root.findall('.//mxCell')
        if len(cells) < 2:  # 至少应该有 id=0 和 id=1 两个基础单元格
            return False, "缺少图形元素 (mxCell)"

        print("[XML验证] XML 验证通过")
        return True, ""

    except Exception as e:
        return False, f"验证异常: {str(e)}"

def clean_xml(xml_string: str) -> str:
    """
    清理和修复 AI 生成的 XML
    """
    try:
        print("[XML清理] 开始清理和验证 XML...")

        # 1. 清理可能的 markdown 标记
        xml_string = xml_string.replace('```xml', '').replace('```', '').strip()

        # 2. 移除重复的属性（最常见的错误）
        # 匹配重复的属性，如 x="100" x="200"
        def remove_duplicate_attrs(match):
            tag_content = match.group(1)

            # 提取所有属性
            attrs = {}
            attr_pattern = r'(\w+)="([^"]*)"'

            for attr_match in re.finditer(attr_pattern, tag_content):
                attr_name = attr_match.group(1)
                attr_value = attr_match.group(2)
                # 保留最后一个值（通常是最新的）
                attrs[attr_name] = attr_value

            # 重建标签
            tag_start = tag_content.split()[0] if ' ' in tag_content else tag_content
            reconstructed = tag_start
            for key, value in attrs.items():
                reconstructed += f' {key}="{value}"'

            return f'<{reconstructed}>'

        # 匹配开始标签
        xml_string = re.sub(r'<([^/>]+)>', remove_duplicate_attrs, xml_string)

        # 3. 验证 XML 是否可解析
        try:
            ET.fromstring(xml_string)
            print("[XML清理] XML 格式验证通过")
        except ET.ParseError as e:
            print(f"[XML清理] 警告: XML 解析错误: {str(e)}")
            print(f"[XML清理] 尝试修复...")

            # 常见错误修复
            # 修复未闭合的标签
            xml_string = xml_string.replace('<br>', '<br/>')
            xml_string = xml_string.replace('<hr>', '<hr/>')

            # 再次尝试解析
            try:
                ET.fromstring(xml_string)
                print("[XML清理] 修复后验证通过")
            except ET.ParseError as e2:
                print(f"[XML清理] 错误: 仍然无法解析: {str(e2)}")
                # 继续返回，让前端尝试处理

        # 4. 确保有完整的 mxfile 结构
        if not xml_string.startswith('<mxfile'):
            if '<mxGraphModel' in xml_string:
                xml_string = f'<mxfile host="app.diagrams.net"><diagram name="Page-1" id="diagram1">{xml_string}</diagram></mxfile>'
                print("[XML清理] 已添加 mxfile 包装")

        print(f"[XML清理] 清理完成，XML 长度: {len(xml_string)} 字符")
        return xml_string

    except Exception as e:
        print(f"[XML清理] 清理过程出错: {str(e)}")
        return xml_string  # 返回原始内容

# ========== API 路由 ==========

# 自定义文档路由（使用国内 CDN 镜像）
@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        swagger_js_url="https://cdn.bootcdn.net/ajax/libs/swagger-ui/5.9.0/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.bootcdn.net/ajax/libs/swagger-ui/5.9.0/swagger-ui.css",
    )

@app.get("/redoc", include_in_schema=False)
async def redoc_html():
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=app.title + " - ReDoc",
        redoc_js_url="https://cdn.bootcdn.net/ajax/libs/redoc/2.1.3/bundles/redoc.standalone.js",
    )

@app.get("/")
async def root():
    return {
        "message": "AI 流程图生成器 API",
        "version": "1.0.0",
        "endpoints": {
            "generate": "/api/generate-diagram",
            "save": "/api/save-diagram",
            "load": "/api/diagram/{id}",
            "test": "/api/test-ai",
            "ai_configs": "/api/ai-configs"
        }
    }

# ========== AI 配置管理 API ==========

@app.get("/api/ai-configs")
async def get_ai_configs():
    """
    获取所有 AI 配置
    """
    try:
        configs = config_manager.get_all_configs()
        return {
            "success": True,
            "total": len(configs),
            "configs": [config.dict() for config in configs]
        }
    except Exception as e:
        print(f"[配置API] 获取配置失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取配置失败: {str(e)}")

@app.get("/api/ai-configs/{config_id}")
async def get_ai_config(config_id: str):
    """
    获取指定 AI 配置
    """
    try:
        config = config_manager.get_config(config_id)
        if not config:
            raise HTTPException(status_code=404, detail="配置不存在")

        return {
            "success": True,
            "config": config.dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[配置API] 获取配置失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"获取配置失败: {str(e)}")

@app.post("/api/ai-configs")
async def create_ai_config(request: AIConfigCreateRequest):
    """
    创建新的 AI 配置
    """
    try:
        config = config_manager.create_config(request)
        return {
            "success": True,
            "message": "配置创建成功",
            "config": config.dict()
        }
    except Exception as e:
        print(f"[配置API] 创建配置失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"创建配置失败: {str(e)}")

@app.put("/api/ai-configs/{config_id}")
async def update_ai_config(config_id: str, request: AIConfigUpdateRequest):
    """
    更新 AI 配置
    """
    try:
        config = config_manager.update_config(config_id, request)
        if not config:
            raise HTTPException(status_code=404, detail="配置不存在")

        return {
            "success": True,
            "message": "配置更新成功",
            "config": config.dict()
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[配置API] 更新配置失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"更新配置失败: {str(e)}")

@app.delete("/api/ai-configs/{config_id}")
async def delete_ai_config(config_id: str):
    """
    删除 AI 配置
    """
    try:
        success = config_manager.delete_config(config_id)
        if not success:
            raise HTTPException(status_code=404, detail="配置不存在")

        return {
            "success": True,
            "message": "配置删除成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[配置API] 删除配置失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"删除配置失败: {str(e)}")

@app.get("/api/test-ai")
async def test_ai():
    """
    测试 AI API 是否可用（调试用）
    """
    results = []
    ai_apis = get_ai_apis()  # 从配置管理器获取配置

    for api_config in ai_apis:
        try:
            print(f"\n[测试] {api_config['name']}")
            print(f"  URL: {api_config['base_url']}/chat/completions")
            print(f"  Model: {api_config['model']}")

            # 发送简单的测试请求
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "model": api_config['model'],
                    "messages": [
                        {"role": "user", "content": "Hello"}
                    ]
                }

                print(f"  请求体: {payload}")

                response = await client.post(
                    f"{api_config['base_url']}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_config['api_key']}",
                        "Content-Type": "application/json"
                    },
                    json=payload
                )

                print(f"  状态码: {response.status_code}")

                if response.status_code == 200:
                    result_data = response.json()
                    content = result_data.get('choices', [{}])[0].get('message', {}).get('content', '')
                    print(f"  响应: {content[:100]}...")

                    results.append({
                        "api": api_config['name'],
                        "status": "成功",
                        "status_code": 200,
                        "response_preview": content[:100]
                    })
                else:
                    error_text = response.text[:200]
                    print(f"  错误: {error_text}")

                    results.append({
                        "api": api_config['name'],
                        "status": "失败",
                        "status_code": response.status_code,
                        "error": error_text
                    })

        except httpx.TimeoutException as e:
            error_msg = f"请求超时: {str(e)}"
            print(f"  异常类型: TimeoutException")
            print(f"  异常详情: {error_msg}")

            results.append({
                "api": api_config['name'],
                "status": "超时",
                "error": error_msg,
                "error_type": "TimeoutException"
            })

        except httpx.ConnectError as e:
            error_msg = f"连接失败: {str(e)}"
            print(f"  异常类型: ConnectError")
            print(f"  异常详情: {error_msg}")

            results.append({
                "api": api_config['name'],
                "status": "连接失败",
                "error": error_msg,
                "error_type": "ConnectError"
            })

        except httpx.HTTPError as e:
            error_msg = f"HTTP 错误: {str(e)}"
            print(f"  异常类型: HTTPError")
            print(f"  异常详情: {error_msg}")

            results.append({
                "api": api_config['name'],
                "status": "HTTP错误",
                "error": error_msg,
                "error_type": "HTTPError"
            })

        except Exception as e:
            import traceback
            error_msg = str(e)
            error_trace = traceback.format_exc()
            print(f"  异常类型: {type(e).__name__}")
            print(f"  异常详情: {error_msg}")
            print(f"  异常堆栈:\n{error_trace}")

            results.append({
                "api": api_config['name'],
                "status": "异常",
                "error": error_msg,
                "error_type": type(e).__name__,
                "traceback": error_trace
            })

    return {"test_results": results}

@app.post("/api/generate-diagram-stream")
async def generate_diagram_stream(request: DiagramGenerateRequest):
    """
    流式生成 draw.io XML（支持实时输出）
    """
    async def event_generator():
        last_error = None
        ai_apis = get_ai_apis()

        for api_config in ai_apis:
            if api_config['name'] in request.skip_apis:
                yield f"data: {json.dumps({'type': 'skip', 'api': api_config['name']}, ensure_ascii=False)}\n\n"
                continue

            try:
                yield f"data: {json.dumps({'type': 'start', 'api': api_config['name']}, ensure_ascii=False)}\n\n"

                # 构建消息历史
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                for msg in request.messages:
                    messages.append({"role": msg.role, "content": msg.content})
                messages.append({"role": "user", "content": request.prompt})

                # 构建请求体 - 启用流式输出
                payload = {
                    "model": api_config['model'],
                    "messages": messages,
                    "temperature": 0.7,
                    "stream": True  # 关键：启用流式输出
                }

                headers = {
                    "Authorization": f"Bearer {api_config['api_key']}",
                    "Content-Type": "application/json"
                }

                # 使用流式请求
                async with httpx.AsyncClient(timeout=120.0) as client:
                    async with client.stream(
                        "POST",
                        f"{api_config['base_url']}/chat/completions",
                        headers=headers,
                        json=payload
                    ) as response:
                        if response.status_code != 200:
                            error_msg = f"API 返回错误: {response.status_code}"
                            yield f"data: {json.dumps({'type': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"
                            continue

                        full_content = ""
                        async for line in response.aiter_lines():
                            if line.startswith("data: "):
                                data_str = line[6:]  # 去掉 "data: " 前缀

                                if data_str == "[DONE]":
                                    break

                                try:
                                    data = json.loads(data_str)
                                    if 'choices' in data and len(data['choices']) > 0:
                                        delta = data['choices'][0].get('delta', {})
                                        content = delta.get('content', '')

                                        if content:
                                            full_content += content
                                            # 发送流式内容给前端
                                            yield f"data: {json.dumps({'type': 'content', 'content': content}, ensure_ascii=False)}\n\n"
                                            await asyncio.sleep(0.01)  # 小延迟，避免过快
                                except json.JSONDecodeError:
                                    continue

                # 流式输出完成，进行XML清理
                cleaned_xml = clean_xml(full_content)

                if not cleaned_xml.strip():
                    print(f"[验证失败] {api_config['name']} 返回空内容")
                    api_name = api_config['name']
                    # 通知前端验证失败
                    yield f"data: {json.dumps({'type': 'validation_failed', 'message': 'XML内容为空，请重试', 'error': 'XML内容为空'}, ensure_ascii=False)}\n\n"
                    return

                # 严格验证 XML
                is_valid, error_msg = validate_xml_strict(cleaned_xml)
                if not is_valid:
                    print(f"[验证失败] {api_config['name']} XML验证失败: {error_msg}")
                    api_name = api_config['name']
                    # 通知前端验证失败
                    yield f"data: {json.dumps({'type': 'validation_failed', 'message': f'XML验证失败: {error_msg}', 'error': error_msg}, ensure_ascii=False)}\n\n"
                    return

                # 验证通过，构建对话历史
                new_messages = []
                for msg in request.messages:
                    new_messages.append({"role": msg.role, "content": msg.content})
                new_messages.append({"role": "user", "content": request.prompt})
                new_messages.append({"role": "assistant", "content": cleaned_xml})

                # 发送完成信号和最终XML
                result = {
                    "type": "complete",
                    "xml": cleaned_xml,
                    "api_used": api_config['name'],
                    "messages": new_messages
                }
                yield f"data: {json.dumps(result, ensure_ascii=False)}\n\n"
                return

            except Exception as e:
                error_msg = f"{api_config['name']} 错误: {str(e)}"
                yield f"data: {json.dumps({'type': 'error', 'message': error_msg}, ensure_ascii=False)}\n\n"
                last_error = error_msg
                continue

        # 所有API都失败
        yield f"data: {json.dumps({'type': 'failed', 'message': f'所有API都失败了: {last_error}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )

@app.post("/api/generate-diagram")
async def generate_diagram(request: DiagramGenerateRequest):
    """
    调用 AI 模型生成 draw.io XML（支持多 API 故障转移 + 对话记忆 + API 切换）
    """
    last_error = None
    ai_apis = get_ai_apis()  # 从配置管理器获取配置

    # 遍历所有配置的 API，按顺序尝试
    for api_config in ai_apis:
        # 检查是否需要跳过此 API
        if api_config['name'] in request.skip_apis:
            print(f"\n[跳过] {api_config['name']} (前端请求跳过)")
            continue

        try:
            print(f"\n{'='*60}")
            print(f"[尝试] 使用 {api_config['name']} 生成流程图")
            print(f"  URL: {api_config['base_url']}/chat/completions")
            print(f"  Model: {api_config['model']}")
            print(f"  用户提示: {request.prompt}")
            print(f"  对话历史: {len(request.messages)} 条消息")
            print(f"{'='*60}\n")

            # 构建消息历史
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

            # 添加历史对话
            for msg in request.messages:
                messages.append({
                    "role": msg.role,
                    "content": msg.content
                })

            # 添加当前用户消息
            messages.append({
                "role": "user",
                "content": request.prompt
            })

            print(f"[调试] 消息数量: {len(messages)} (包含系统提示词)")

            # 构建请求体
            payload = {
                "model": api_config['model'],
                "messages": messages,
                "temperature": 0.7
                # 不设置 max_tokens 限制，让模型自由生成
            }

            print(f"[调试] 请求体（前200字符）: {str(payload)[:200]}...")

            # 构建请求头
            headers = {
                "Authorization": f"Bearer {api_config['api_key']}",
                "Content-Type": "application/json"
            }

            # 打印请求头（隐藏完整的 API Key）
            print(f"[调试] 请求头:")
            print(f"  - Content-Type: {headers['Content-Type']}")
            print(f"  - Authorization: Bearer {api_config['api_key'][:15]}...（已隐藏）")

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{api_config['base_url']}/chat/completions",
                    headers=headers,
                    json=payload
                )

            print(f"[调试] 响应状态码: {response.status_code}")

            # 检查响应状态
            if response.status_code != 200:
                error_msg = f"{api_config['name']} API 返回错误: {response.status_code}"
                error_detail = response.text[:300]
                print(f"[失败] {error_msg}")
                print(f"[失败] 错误详情: {error_detail}")
                last_error = f"{error_msg} - {error_detail}"
                continue  # 尝试下一个 API

            # 解析响应
            result = response.json()

            # 检查响应格式
            if 'choices' not in result or len(result['choices']) == 0:
                error_msg = f"{api_config['name']} 返回格式错误: {result}"
                print(f"[失败] {error_msg}")
                last_error = error_msg
                continue

            xml = result['choices'][0]['message']['content']

            print(f"[调试] AI 返回的原始 XML 长度: {len(xml)} 字符")

            # 使用 XML 清理和验证函数
            xml = clean_xml(xml)

            # 最后验证
            if not xml.strip():
                error_msg = f"{api_config['name']} 返回空内容"
                print(f"[失败] {error_msg}")
                last_error = error_msg
                continue

            # 成功生成
            print(f"[成功] 使用 {api_config['name']} 成功生成流程图！")
            print(f"[成功] 最终 XML 长度: {len(xml)} 字符")

            # 构建新的对话历史
            new_messages = []
            for msg in request.messages:
                new_messages.append({
                    "role": msg.role,
                    "content": msg.content
                })

            # 添加本次对话
            new_messages.append({
                "role": "user",
                "content": request.prompt
            })
            new_messages.append({
                "role": "assistant",
                "content": xml  # AI 返回的 XML
            })

            return {
                "xml": xml,
                "prompt": request.prompt,
                "api_used": api_config['name'],
                "messages": new_messages  # 返回完整的对话历史
            }

        except httpx.TimeoutException as e:
            error_msg = f"{api_config['name']} 请求超时"
            print(f"[超时] {error_msg}")
            print(f"[超时] 详情: {str(e)}")
            last_error = f"{error_msg}: {str(e)}"
            continue

        except httpx.ConnectError as e:
            error_msg = f"{api_config['name']} 连接失败"
            print(f"[连接错误] {error_msg}")
            print(f"[连接错误] 详情: {str(e)}")
            print(f"[连接错误] 可能原因: 网络不可达或DNS解析失败")
            last_error = f"{error_msg}: {str(e)}"
            continue

        except httpx.HTTPError as e:
            error_msg = f"{api_config['name']} HTTP错误"
            print(f"[HTTP错误] {error_msg}")
            print(f"[HTTP错误] 详情: {str(e)}")
            last_error = f"{error_msg}: {str(e)}"
            continue

        except Exception as e:
            import traceback
            error_msg = f"{api_config['name']} 未知错误: {type(e).__name__}"
            error_trace = traceback.format_exc()
            print(f"[错误] {error_msg}")
            print(f"[错误] 详情: {str(e)}")
            print(f"[错误] 堆栈:\n{error_trace}")
            last_error = f"{error_msg}: {str(e)}"
            continue

    # 所有 API 都失败了
    print(f"[失败] 所有 API 都无法使用")
    raise HTTPException(
        status_code=500,
        detail=f"所有 AI API 都失败了。最后一个错误: {last_error}"
    )

@app.post("/api/save-diagram")
async def save_diagram(request: DiagramSaveRequest):
    """
    保存图表到数据库
    """
    global diagram_counter

    diagram_id = str(diagram_counter)
    diagram_counter += 1

    diagram = {
        "id": diagram_id,
        "xml": request.xml,
        "name": request.name or f"流程图 {diagram_id}",
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }

    diagrams_db[diagram_id] = diagram

    return {
        "id": diagram_id,
        "message": "保存成功",
        "name": diagram["name"]
    }

@app.get("/api/diagram/{diagram_id}")
async def get_diagram(diagram_id: str):
    """
    加载已保存的图表
    """
    if diagram_id not in diagrams_db:
        raise HTTPException(status_code=404, detail="图表不存在")

    return diagrams_db[diagram_id]

@app.put("/api/diagram/{diagram_id}")
async def update_diagram(diagram_id: str, request: DiagramSaveRequest):
    """
    更新已有图表（支持二次编辑）
    """
    if diagram_id not in diagrams_db:
        raise HTTPException(status_code=404, detail="图表不存在")

    diagrams_db[diagram_id]["xml"] = request.xml
    diagrams_db[diagram_id]["updated_at"] = datetime.now().isoformat()

    if request.name:
        diagrams_db[diagram_id]["name"] = request.name

    return {
        "id": diagram_id,
        "message": "更新成功"
    }

@app.get("/api/diagrams")
async def list_diagrams():
    """
    获取所有图表列表
    """
    return {
        "total": len(diagrams_db),
        "diagrams": [
            {
                "id": d["id"],
                "name": d["name"],
                "created_at": d["created_at"]
            }
            for d in diagrams_db.values()
        ]
    }

@app.delete("/api/diagram/{diagram_id}")
async def delete_diagram(diagram_id: str):
    """
    删除图表
    """
    if diagram_id not in diagrams_db:
        raise HTTPException(status_code=404, detail="图表不存在")

    del diagrams_db[diagram_id]

    return {"message": "删除成功"}

# ========== 健康检查 ==========

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "diagrams_count": len(diagrams_db)
    }

if __name__ == "__main__":
    import uvicorn
    import os

    # 开发模式：启用热重载
    # 生产模式：禁用热重载
    dev_mode = os.getenv("DEV_MODE", "true").lower() == "true"

    if dev_mode:
        print("[开发模式] 热重载已启用")
        print("[提示] 修改代码后会自动重启服务器")
        print("[警告] 生产环境请设置 DEV_MODE=false")
        print("")

    uvicorn.run(
        "main:app",  # 使用字符串形式，支持热重载
        host="0.0.0.0",
        port=8000,
        reload=dev_mode,  # 开发模式启用热重载
        reload_includes=["*.py"],  # 监控 .py 文件
        log_level="info"
    )
