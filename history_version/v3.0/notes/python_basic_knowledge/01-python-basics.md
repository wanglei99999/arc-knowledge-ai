# 12 — Python 语法补充：看懂抽象类与类型标注

**对应代码**：
- `app/pipeline/core/stage.py`
- `app/pipeline/core/hook.py`
- `app/pipeline/strategies/base_strategy.py`
- `app/pipeline/core/pipeline.py`

## 读完本文你能回答

- `ABC` 是什么？为什么 `BaseStage` 要继承它？
- `TypeVar`、`Generic`、`ClassVar` 分别是干什么的？
- `class BaseStage(ABC, Generic[TInput, TOutput])` 这整句应该怎么读？
- 以后再遇到陌生 Python 语法，怎么继续把它补到这份文档里？

---

## 为什么这里会有这些语法

这个项目不是“脚本式”写法，而是在写一个可扩展的处理框架。

所以代码里会经常看到两类东西：

1. **抽象类**：先定义规范，再让子类去实现
2. **类型标注**：把“输入是什么、输出是什么、这是类变量还是实例变量”写清楚

`BaseStage` 这一句正好把这两类都用上了：

```python
class BaseStage(ABC, Generic[TInput, TOutput]):
```

---

## 1. `ABC`：抽象基类

`ABC` 来自 `abc` 模块，完整导入方式是：

```python
from abc import ABC, abstractmethod
```

它的作用是：**把一个类声明成“抽象类”**。

抽象类一般不是拿来直接实例化的，而是拿来定义“子类必须遵守的接口”。

最小例子：

```python
from abc import ABC, abstractmethod


class Animal(ABC):
    @abstractmethod
    def speak(self) -> str:
        ...


class Dog(Animal):
    def speak(self) -> str:
        return "wang"
```

这里：

- `Animal` 是抽象类
- `speak()` 是抽象方法
- `Dog` 必须实现 `speak()`
- 如果子类不实现抽象方法，就不能正常实例化

放到项目里看，`BaseStage` 的意思就是：

- 它先定义“Stage 应该长什么样”
- 具体怎么解析、切片、向量化，交给子类去写

---

## 2. `@abstractmethod`：要求子类必须实现

它通常和 `ABC` 配套出现：

```python
@abstractmethod
async def _execute(self, ctx: ProcessingContext, input: TInput) -> TOutput:
    ...
```

这表示：

- `BaseStage` 只规定 `_execute()` 这个方法必须存在
- 但不提供真正的业务实现
- 真正的逻辑由 `ParserStage`、`TokenChunkerStage`、`EmbedStage` 等子类补上
---

## 3. `TypeVar`：类型占位符

导入方式：

```python
from typing import TypeVar
```

定义方式：

```python
TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")
```

它的意思是：先占住一个“类型位置”，以后再决定具体是什么类型。

比如：

```python
T = TypeVar("T")
```

这里的 `T` 现在还不是 `int`、`str` 或 `User`，它只是一个“以后会被具体化的类型变量”。

在项目里：

```python
TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")
```

表示：

- `BaseStage` 的输入类型先留空
- `BaseStage` 的输出类型也先留空
- 等子类继承时再填上

例如：

```python
class ParserStage(BaseStage[RawFile, ParsedDocument]):
    ...
```

这里就把：

- `TInput` 填成了 `RawFile`
- `TOutput` 填成了 `ParsedDocument`

---

## 4. `Generic`：让类支持泛型参数

导入方式：

```python
from typing import Generic
```

`Generic` 要和 `TypeVar` 一起看。

最小例子：

```python
from typing import Generic, TypeVar

T = TypeVar("T")


class Box(Generic[T]):
    def __init__(self, value: T) -> None:
        self.value = value
```

它表示 `Box` 不是某一种固定类型的盒子，而是“可以装不同类型”的泛型盒子：

```python
Box[int]
Box[str]
```

放到项目里：

```python
class BaseStage(ABC, Generic[TInput, TOutput]):
```

意思就是：

- `BaseStage` 是一个抽象类
- 同时它还是一个泛型类
- 它有两个可变的类型参数：输入类型、输出类型

所以后面才能写出：

```python
class TokenChunkerStage(BaseStage[ParsedDocument, list[DocumentChunk]]):
    ...
```

这能让阅读代码的人一眼看出：

- 它输入的是 `ParsedDocument`
- 它输出的是 `list[DocumentChunk]`

---

## 5. `ClassVar`：这是类变量，不是实例变量

导入方式：

```python
from typing import ClassVar
```

看这个例子：

```python
class User:
    role: ClassVar[str] = "admin"

    def __init__(self, name: str) -> None:
        self.name = name
```

这里：

- `role` 是类变量，属于 `User` 这个类本身
- `name` 是实例变量，每个对象各有一份

也就是说：

```python
User.role
```

是全类共享的，

```python
user.name
```

是某个实例自己的数据。

在 `BaseStage` 里：

```python
name: ClassVar[str]
version: ClassVar[str] = "1.0"
requires: ClassVar[frozenset[str]] = frozenset()
produces: ClassVar[frozenset[str]] = frozenset()
```

这些都不是“某个 Stage 实例独有的数据”，而是“这个 Stage 类本身的声明信息”：

- `name`：注册名
- `version`：版本号
- `requires`：执行前需要哪些上下文 key
- `produces`：执行后会产出哪些上下文 key

它们更像“类配置”或“类元数据”。

---

## 6. 把这句完整读出来

```python
class BaseStage(ABC, Generic[TInput, TOutput]):
```

可以把它读成：

> `BaseStage` 是一个抽象基类，并且它是一个带两个类型参数的泛型类。

再结合下面这些字段：

```python
name: ClassVar[str]
requires: ClassVar[frozenset[str]] = frozenset()
produces: ClassVar[frozenset[str]] = frozenset()
```

完整意思就是：

- `BaseStage` 定义了一套 Stage 规范
- 子类必须实现 `_execute()`
- 每个子类都要声明自己的输入输出类型
- 每个子类还可以声明自己的类级别元信息

---

## 7. 在这个项目里分别出现在哪

### `ABC`

```python
class BaseStage(ABC):
class BaseHook(ABC):
class BaseStrategy(ABC):
class BaseProvider(ABC):
```

这些类都在表达同一个意思：这是“规范”或“接口层”，不是具体实现。

### `TypeVar` + `Generic`

```python
TInput = TypeVar("TInput")
TOutput = TypeVar("TOutput")

class BaseStage(ABC, Generic[TInput, TOutput]):
    ...
```

```python
class Pipeline(Generic[TInput, TOutput]):
    ...
```

这些是在表达：框架代码本身不绑死具体数据类型。

### `ClassVar`

```python
name: ClassVar[str]
phase: ClassVar[Phase | list[Phase]]
strategy_id: ClassVar[str]
provider_id: ClassVar[str]
```

这些是在表达：某些字段是“类级别声明”，不是运行时某个对象自己的状态。

---

## 8. 一个快速判断口诀

| 看到什么 | 大概率在表达什么 |
|------|------|
| `ABC` | 这是抽象类，主要定义规范 |
| `@abstractmethod` | 子类必须实现这个方法 |
| `TypeVar("T")` | 先占一个类型坑位 |
| `Generic[T]` | 这个类支持泛型参数 |
| `ClassVar[str]` | 这是类变量，不是实例字段 |

---

## 9. 后面可以继续补哪些 Python 知识

如果你继续沿着这个项目学，下一批很可能会碰到：

- `from __future__ import annotations`
- `async def` / `await`
- `list[str]`、`dict[str, Any]`
- `@dataclass`
- `Protocol`
- `Enum`
- `|` 联合类型，例如 `Phase | list[Phase]`

可以继续往这篇里追加。

---

## 10. 可复用的补充模板

以后你看到一个陌生语法，可以直接照这个格式往下补：

~~~~md
## 主题：语法名 / 关键字

**出现位置**：
- `app/xxx.py`

**原始代码**：
```python
# 把原句贴在这里
```

**一句话解释**：

**它是干什么的**：

**为什么这里要这样写**：

**最小例子**：
```python
# 写一个 5~10 行的小例子
```

**容易混淆的点**：

**和相近概念的区别**：
~~~~

---

## 11. 先把你刚问到的几项记成一句话

- `ABC`：把类标记成抽象类，用来定义规范
- `@abstractmethod`：要求子类必须实现某个方法
- `TypeVar`：定义类型占位符
- `Generic`：让类支持类型参数
- `ClassVar`：标记这是类变量，不是实例变量

如果你以后再遇到 `Protocol`、`Annotated`、`Self` 这类写法，也可以继续加在这篇下面。

---

## 12. `with` 语法：在一段代码里临时启用某种上下文

你刚才看到的代码是：

```python
with workflow.unsafe.imports_passed_through():
    from app.workflows.ingestion_activities import (
        IngestionInput,
        chunk_activity,
        embed_and_index_activity,
        parse_activity,
    )
```

这里的 `with` 不是循环，也不是判断。

它的核心意思是：

> 进入一个“受管理的上下文”，在这段代码执行前后自动做一些事情。

---

### 最常见的 `with`：打开文件

最经典的例子：

```python
with open("a.txt", "r", encoding="utf-8") as f:
    text = f.read()
```

它等价于一种“先打开，用完再关”的模式：

```python
f = open("a.txt", "r", encoding="utf-8")
try:
    text = f.read()
finally:
    f.close()
```

所以 `with` 的价值是：

- 自动清理资源
- 即使中间报错，也能执行收尾动作
- 代码更短，也更不容易忘记释放资源

---

### `with ... as ...` 里的 `as` 是什么

```python
with open("a.txt", "r", encoding="utf-8") as f:
    text = f.read()
```

这里：

- `open(...)` 返回一个“上下文管理对象”
- `as f` 表示把这个对象交给变量 `f`

不是所有 `with` 都必须写 `as`。

例如你刚才看到的：

```python
with workflow.unsafe.imports_passed_through():
    ...
```

这里就不需要把对象取名，所以没有 `as xxx`。

---

### `with` 背后是什么机制

`with` 本质上依赖“上下文管理器”。

一个对象如果实现了这两个方法，就可以配合 `with` 使用：

```python
__enter__()
__exit__()
```

你可以先粗略理解成：

- `__enter__()`：进入 `with` 代码块前执行
- `__exit__()`：离开 `with` 代码块后执行

最小例子：

```python
class Demo:
    def __enter__(self):
        print("进入")
        return self

    def __exit__(self, exc_type, exc, tb):
        print("退出")


with Demo() as d:
    print("执行中")
```

执行顺序大致是：

```python
obj = Demo()
d = obj.__enter__()
try:
    print("执行中")
finally:
    obj.__exit__(...)
```

---

### 为什么 `with` 容易让人迷糊

因为它表面上只是一行：

```python
with something():
    ...
```

但实际上它在表达三件事：

1. 进入前，先做初始化
2. 中间，执行代码块
3. 结束后，不管报不报错，都做清理

所以看到 `with` 时，最好的心里翻译是：

> 在这个代码块里，临时使用某种规则或资源，并在结束时自动收尾。

---

### 放到 Temporal 这段代码里怎么理解

```python
with workflow.unsafe.imports_passed_through():
    from app.workflows.ingestion_activities import (
        IngestionInput,
        chunk_activity,
        embed_and_index_activity,
        parse_activity,
    )
```

这里可以理解成：

- 进入这个 `with` 块时
- Temporal 临时放宽对这些 `import` 的检查规则
- 让这几个导入“直接放行”
- 代码块结束后，恢复原来的处理方式

所以它不是在说“循环导入”或者“条件导入”，而是在说：

> 在这段代码里，用一个特殊上下文执行 `import`。

---

### 再看几个常见场景

#### 锁

```python
with lock:
    shared_list.append(1)
```

意思是：

- 进入代码块时自动加锁
- 退出代码块时自动释放锁

#### 数据库事务

```python
with session.begin():
    session.add(user)
```

意思通常是：

- 进入代码块时开启事务
- 正常结束就提交
- 出异常就回滚

#### 临时规则

```python
with decimal.localcontext() as ctx:
    ctx.prec = 4
    ...
```

意思是：

- 在这段代码里临时修改精度规则
- 出了这个块就恢复

Temporal 这个例子就更接近“临时规则”这一类。

---

### 一个快速判断口诀

看到：

```python
with X():
    ...
```

你就问自己三件事：

1. 进入前做了什么？
2. 退出后做了什么？
3. 这个上下文是“资源管理”还是“临时规则”？

如果想通这三点，`with` 基本就不迷糊了。

---

### 一句话记住

- `with`：在一个受管理的上下文里执行代码
- 常见用途：文件、锁、事务、临时配置、框架特殊规则
- 核心价值：自动进入、自动收尾、异常时也能安全清理
