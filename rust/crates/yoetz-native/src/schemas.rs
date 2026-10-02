//! `yoetz.protocol.schemas._ValidityChecker` decided natively.
//!
//! The packaged catalog is compiled once into a node graph whose `$ref`s are resolved up front,
//! exactly as `referencing` resolves them for a catalog without nested `$id`s, anchors or
//! dynamic references. Every keyword the catalog uses is implemented with `jsonschema` 4.26's
//! draft 2020-12 semantics (`_keywords.py`, `_utils.equal`/`uniq`/
//! `find_evaluated_property_keys_by_schema`, `_types`). A catalog holding any keyword or
//! construct this module does not implement refuses to compile, so the Python checker stays in
//! charge of it unchanged.
//!
//! A verdict is three-valued. `Some(true)` means the stock `Draft202012Validator` accepts the
//! instance, `Some(false)` that it rejects it (or raises), and `None` that only Python can tell:
//! an instance value that is not an exact JSON type, a pattern or `date-time` whose Python answer
//! failed, or an evaluation deep enough that Python's own recursion limit could decide it.

use std::collections::{HashMap, HashSet};
use std::sync::Mutex;

use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyDict, PyList, PyString, PyTuple};
use regex::Regex;
use yoetz_core::protocol::schemas::{compile_python_pattern, is_rfc3339_date_time};

type NodeId = u32;

const ANY: NodeId = 0;
const NEVER: NodeId = 1;

/// Nested schema applications one verdict may make before it defers to Python. The stock
/// validator spends several Python frames per application (more where a collapsed bare `$ref`
/// stood), so a deeper evaluation could end in its `RecursionError` (a rejection) where a native
/// walk would accept. The packaged catalog's deepest acyclic chain is 24 applications.
const MAX_APPLICATION_DEPTH: u32 = 48;

/// Container nesting the raw-value check walks; canonical values never nest deeper than 64.
const MAX_PLAIN_DEPTH: usize = 80;

/// The draft 2020-12 keywords `jsonschema` 4.26 applies (`Draft202012Validator.VALIDATORS`). The
/// mirrored class's own keyword list is passed in at compile time; one outside this list refuses.
const KNOWN_KEYWORDS: &[&str] = &[
    "$dynamicRef", "$ref", "additionalProperties", "allOf", "anyOf", "const", "contains",
    "dependentRequired", "dependentSchemas", "enum", "exclusiveMaximum", "exclusiveMinimum",
    "format", "if", "items", "maxItems", "maxLength", "maxProperties", "maximum", "minItems",
    "minLength", "minProperties", "minimum", "multipleOf", "not", "oneOf", "pattern",
    "patternProperties", "prefixItems", "properties", "propertyNames", "required", "type",
    "unevaluatedItems", "unevaluatedProperties", "uniqueItems",
];

/// Keys that change reference resolution or that this module does not implement.
const REFUSED_KEYWORDS: &[&str] = &[
    "$dynamicRef", "$dynamicAnchor", "$anchor", "$recursiveRef", "$recursiveAnchor", "$vocabulary",
    "$schema", "dependentSchemas", "patternProperties", "multipleOf", "unevaluatedItems",
];

// Instance kinds, for the per-node set of kinds a node can accept.
const KIND_NULL: u8 = 1;
const KIND_BOOL: u8 = 2;
const KIND_INT: u8 = 4;
const KIND_STR: u8 = 8;
const KIND_ARRAY: u8 = 16;
const KIND_OBJECT: u8 = 32;
const KIND_ALL: u8 = 63;

const TYPE_NULL: u8 = 1;
const TYPE_BOOLEAN: u8 = 2;
const TYPE_INTEGER: u8 = 4;
const TYPE_NUMBER: u8 = 8;
const TYPE_STRING: u8 = 16;
const TYPE_ARRAY: u8 = 32;
const TYPE_OBJECT: u8 = 64;

/// Neither verdict can be given natively; Python decides.
struct Defer;

impl From<PyErr> for Defer {
    fn from(_: PyErr) -> Self {
        Defer
    }
}

type Verdict<T> = Result<T, Defer>;

/// The catalog holds a construct the native checker does not reproduce.
struct Refuse;

impl From<PyErr> for Refuse {
    fn from(_: PyErr) -> Self {
        Refuse
    }
}

/// A JSON value from the schema (`const`, `enum`).
enum Constant {
    Null,
    Bool(bool),
    Int(i64),
    Str(String),
    Array(Vec<Constant>),
    Object(Vec<(String, Constant)>),
}

enum Pattern {
    Native(Regex),
    Python(Py<PyAny>),
}

#[derive(Default)]
struct Keywords {
    types: Option<u8>,
    constant: Option<Constant>,
    choices: Option<Vec<Constant>>,
    min_length: Option<i64>,
    max_length: Option<i64>,
    pattern: Option<usize>,
    date_time: bool,
    minimum: Option<i64>,
    maximum: Option<i64>,
    exclusive_minimum: Option<i64>,
    exclusive_maximum: Option<i64>,
    // Objects.
    /// Ordered cheapest first; the first `light_properties` are checked before any applicator.
    properties: Vec<(Py<PyString>, NodeId)>,
    light_properties: usize,
    property_set: Option<Py<PyDict>>,
    required: Vec<Py<PyString>>,
    additional: Option<NodeId>,
    unevaluated: Option<NodeId>,
    min_properties: Option<i64>,
    max_properties: Option<i64>,
    dependent_required: Vec<(Py<PyString>, Vec<Py<PyString>>)>,
    property_names: Option<NodeId>,
    // Arrays.
    prefix_items: Vec<NodeId>,
    items: Option<NodeId>,
    min_items: Option<i64>,
    max_items: Option<i64>,
    unique: bool,
    contains: Option<NodeId>,
    min_contains: i64,
    max_contains: Option<i64>,
    // Applicators.
    reference: Option<NodeId>,
    all_of: Vec<NodeId>,
    any_of: Vec<NodeId>,
    one_of: Vec<NodeId>,
    not: Option<NodeId>,
    condition: Option<NodeId>,
    then: Option<NodeId>,
    otherwise: Option<NodeId>,
}

enum Node {
    Any,
    Never,
    Schema(Box<Keywords>),
}

/// The compiled catalog: one root node per schema id.
#[pyclass(frozen, module = "yoetz_native")]
pub struct SchemaValidity {
    nodes: Vec<Node>,
    roots: HashMap<String, NodeId>,
    patterns: Vec<Pattern>,
    /// Per node, the instance kinds (`KIND_*`) it can possibly accept.
    kinds: Vec<u8>,
    date_time: Py<PyAny>,
    /// Native `pattern` answers by (pattern, text). A page's strings are checked once per
    /// document that embeds it (result model, control envelope, client parse, bridge).
    matches: Mutex<MatchMemory>,
}

#[derive(Default)]
struct MatchMemory {
    /// One map per compiled pattern.
    by_pattern: Vec<HashMap<Box<str>, bool>>,
    entries: usize,
}

/// Entries the pattern-answer memory holds before it starts over.
const MAX_REMEMBERED_MATCHES: usize = 1 << 16;

static MAPPING_ABC: PyOnceLock<Py<PyAny>> = PyOnceLock::new();

/// `_actual_mapping`: `issubclass(type(value), Mapping)`, any failure meaning `False`.
fn is_actual_mapping(py: Python<'_>, value: &Bound<'_, PyAny>) -> bool {
    let Ok(mapping) = MAPPING_ABC
        .get_or_try_init(py, || -> PyResult<Py<PyAny>> { Ok(py.import("collections.abc")?.getattr("Mapping")?.unbind()) })
    else {
        return false;
    };
    let result = unsafe { ffi::PyObject_IsSubclass(value.get_type().as_ptr(), mapping.as_ptr()) };
    if result < 0 {
        let _ = PyErr::take(py);
        return false;
    }
    result == 1
}

#[inline]
fn exact(value: &Bound<'_, PyAny>, check: unsafe fn(*mut ffi::PyObject) -> i32) -> bool {
    unsafe { check(value.as_ptr()) != 0 }
}

/// An instance value as the stock validator types it.
enum Value<'py> {
    Null,
    Bool(bool),
    Int(i64),
    Str(Bound<'py, PyString>),
    Array(Bound<'py, PyAny>),
    Object(Bound<'py, PyDict>),
}

struct Context<'py> {
    py: Python<'py>,
    /// Whether the instance is the unconverted value `_plain_validation_instance` would turn
    /// into the validator's instance (any mapping is an object, a tuple an array).
    raw: bool,
    depth: u32,
    /// Plain dicts built from non-dict mappings, by the mapping's address.
    converted: HashMap<usize, Bound<'py, PyDict>>,
}

impl<'py> Context<'py> {
    /// The `KIND_*` bit of *item* as `value` would read it, or `KIND_ALL` when only `value` can
    /// tell (it may defer).
    fn kind(&self, item: &Bound<'py, PyAny>) -> u8 {
        let pointer = item.as_ptr();
        unsafe {
            if pointer == ffi::Py_None() {
                KIND_NULL
            } else if ffi::PyBool_Check(pointer) != 0 {
                KIND_BOOL
            } else if ffi::PyLong_CheckExact(pointer) != 0 {
                KIND_INT
            } else if ffi::PyUnicode_CheckExact(pointer) != 0 {
                KIND_STR
            } else if ffi::PyDict_CheckExact(pointer) != 0 {
                KIND_OBJECT
            } else if ffi::PyList_CheckExact(pointer) != 0 || (self.raw && ffi::PyTuple_CheckExact(pointer) != 0) {
                KIND_ARRAY
            } else {
                KIND_ALL
            }
        }
    }

    /// Every member of *item* is a value `value` reads, with exact `str` object keys.
    fn ensure_plain(&mut self, item: &Bound<'py, PyAny>, depth: usize) -> Verdict<()> {
        if depth > MAX_PLAIN_DEPTH {
            return Err(Defer);
        }
        // Exact dicts, lists and scalars, the common case, are walked over borrowed pointers.
        match unsafe { plain_fast(item.as_ptr(), depth) } {
            Fast::Plain => return Ok(()),
            Fast::Other => return Err(Defer),
            Fast::Slow => {}
        }
        match self.value(item)? {
            Value::Array(array) => {
                for index in 0..array_len(&array) {
                    self.ensure_plain(&array_item(&array, index)?, depth + 1)?;
                }
            }
            Value::Object(object) => {
                for (key, member) in object.iter() {
                    if !exact(&key, ffi::PyUnicode_CheckExact) {
                        return Err(Defer);
                    }
                    self.ensure_plain(&member, depth + 1)?;
                }
            }
            _ => {}
        }
        Ok(())
    }

    fn value(&mut self, item: &Bound<'py, PyAny>) -> Verdict<Value<'py>> {
        let pointer = item.as_ptr();
        if item.is_none() {
            return Ok(Value::Null);
        }
        if unsafe { ffi::PyBool_Check(pointer) } != 0 {
            return Ok(Value::Bool(pointer == unsafe { ffi::Py_True() }));
        }
        if exact(item, ffi::PyLong_CheckExact) {
            let mut overflow: std::os::raw::c_int = 0;
            let number = unsafe { ffi::PyLong_AsLongLongAndOverflow(pointer, &mut overflow) };
            if overflow != 0 || (number == -1 && PyErr::take(self.py).is_some()) {
                return Err(Defer);
            }
            return Ok(Value::Int(number));
        }
        if exact(item, ffi::PyUnicode_CheckExact) {
            return Ok(Value::Str(unsafe { item.cast_unchecked::<PyString>() }.clone()));
        }
        if exact(item, ffi::PyDict_CheckExact) {
            return Ok(Value::Object(unsafe { item.cast_unchecked::<PyDict>() }.clone()));
        }
        if exact(item, ffi::PyList_CheckExact) {
            return Ok(Value::Array(item.clone()));
        }
        if !self.raw {
            return Err(Defer);
        }
        if is_actual_mapping(self.py, item) {
            let address = pointer as usize;
            if let Some(dict) = self.converted.get(&address) {
                return Ok(Value::Object(dict.clone()));
            }
            // `_plain_validation_instance` rebuilds a mapping from its `.items()`.
            let dict = PyDict::new(self.py);
            for pair in item.call_method0("items")?.try_iter()? {
                let (key, member): (Bound<'py, PyAny>, Bound<'py, PyAny>) = pair?.extract()?;
                dict.set_item(key, member)?;
            }
            self.converted.insert(address, dict.clone());
            return Ok(Value::Object(dict));
        }
        if exact(item, ffi::PyTuple_CheckExact) {
            return Ok(Value::Array(item.clone()));
        }
        Err(Defer)
    }
}

enum Fast {
    /// Every member is an exact JSON scalar, exact `list`, or exact `dict` with exact `str` keys.
    Plain,
    /// A member `Context::value` defers on, whatever the mode.
    Other,
    /// A member only the general walk can judge (a tuple or a non-dict mapping in raw mode).
    Slow,
}

/// `Context::ensure_plain` for exact built-in containers, over borrowed references.
///
/// # Safety
///
/// *item* must be a live object; the GIL is held and no Python code runs during the walk.
unsafe fn plain_fast(item: *mut ffi::PyObject, depth: usize) -> Fast {
    if depth > MAX_PLAIN_DEPTH {
        return Fast::Other;
    }
    unsafe {
        if item == ffi::Py_None() || ffi::PyBool_Check(item) != 0 || ffi::PyUnicode_CheckExact(item) != 0 {
            return Fast::Plain;
        }
        if ffi::PyLong_CheckExact(item) != 0 {
            let mut overflow: std::os::raw::c_int = 0;
            ffi::PyLong_AsLongLongAndOverflow(item, &mut overflow);
            return if overflow == 0 { Fast::Plain } else { Fast::Other };
        }
        if ffi::PyList_CheckExact(item) != 0 {
            let length = ffi::PyList_GET_SIZE(item);
            for index in 0..length {
                match plain_fast(ffi::PyList_GET_ITEM(item, index), depth + 1) {
                    Fast::Plain => {}
                    other => return other,
                }
            }
            return Fast::Plain;
        }
        if ffi::PyDict_CheckExact(item) != 0 {
            let mut position: ffi::Py_ssize_t = 0;
            let mut key: *mut ffi::PyObject = std::ptr::null_mut();
            let mut member: *mut ffi::PyObject = std::ptr::null_mut();
            while ffi::PyDict_Next(item, &mut position, &mut key, &mut member) != 0 {
                if ffi::PyUnicode_CheckExact(key) == 0 {
                    return Fast::Other;
                }
                match plain_fast(member, depth + 1) {
                    Fast::Plain => {}
                    other => return other,
                }
            }
            return Fast::Plain;
        }
    }
    Fast::Slow
}

fn array_len(array: &Bound<'_, PyAny>) -> usize {
    if exact(array, ffi::PyList_CheckExact) {
        unsafe { array.cast_unchecked::<PyList>() }.len()
    } else {
        unsafe { array.cast_unchecked::<PyTuple>() }.len()
    }
}

fn array_item<'py>(array: &Bound<'py, PyAny>, index: usize) -> Verdict<Bound<'py, PyAny>> {
    if exact(array, ffi::PyList_CheckExact) {
        Ok(unsafe { array.cast_unchecked::<PyList>() }.get_item(index)?)
    } else {
        Ok(unsafe { array.cast_unchecked::<PyTuple>() }.get_item(index)?)
    }
}

fn string_length(text: &Bound<'_, PyString>) -> i64 {
    unsafe { ffi::PyUnicode_GetLength(text.as_ptr()) as i64 }
}

fn type_matches(types: u8, value: &Value<'_>) -> bool {
    let bit = match value {
        Value::Null => TYPE_NULL,
        Value::Bool(_) => TYPE_BOOLEAN,
        Value::Int(_) => TYPE_INTEGER | TYPE_NUMBER,
        Value::Str(_) => TYPE_STRING,
        Value::Array(_) => TYPE_ARRAY,
        Value::Object(_) => TYPE_OBJECT,
    };
    types & bit != 0
}

impl SchemaValidity {
    fn valid<'py>(&self, cx: &mut Context<'py>, node: NodeId, item: &Bound<'py, PyAny>) -> Verdict<bool> {
        match &self.nodes[node as usize] {
            Node::Any => Ok(true),
            Node::Never => Ok(false),
            Node::Schema(keywords) => {
                let accepted = self.kinds[node as usize];
                if accepted != KIND_ALL && accepted & cx.kind(item) == 0 {
                    // No instance of this kind satisfies the node.
                    return Ok(false);
                }
                if cx.depth >= MAX_APPLICATION_DEPTH {
                    return Err(Defer);
                }
                cx.depth += 1;
                let verdict = self.keywords_valid(cx, keywords, item);
                cx.depth -= 1;
                verdict
            }
        }
    }

    fn keywords_valid<'py>(&self, cx: &mut Context<'py>, kw: &Keywords, item: &Bound<'py, PyAny>) -> Verdict<bool> {
        let value = cx.value(item)?;
        if let Some(types) = kw.types {
            if !type_matches(types, &value) {
                return Ok(false);
            }
        }
        if let Some(constant) = &kw.constant {
            if !self.equals_constant(cx, &value, constant)? {
                return Ok(false);
            }
        }
        if let Some(choices) = &kw.choices {
            let mut found = false;
            for choice in choices {
                if self.equals_constant(cx, &value, choice)? {
                    found = true;
                    break;
                }
            }
            if !found {
                return Ok(false);
            }
        }
        // Validity does not depend on keyword order, so cheap checks that usually decide a
        // failing `anyOf`/`oneOf` branch (scalars, `required`, shallow properties) run before
        // applicators and deep members.
        let fits = match &value {
            Value::Str(text) => self.string_valid(cx, kw, text)?,
            Value::Int(number) => number_valid(kw, *number),
            Value::Array(array) => self.array_valid(cx, kw, array)?,
            Value::Object(object) => {
                let Some(present) = self.object_shallow(cx, kw, object)? else {
                    return Ok(false);
                };
                if !self.applicators_valid(cx, kw, item)? {
                    return Ok(false);
                }
                return self.object_deep(cx, kw, object, present);
            }
            Value::Null | Value::Bool(_) => true,
        };
        if !fits {
            return Ok(false);
        }
        self.applicators_valid(cx, kw, item)
    }

    fn native_match(&self, pattern: usize, regex: &Regex, text: &str) -> bool {
        // `try_lock`: a concurrent check simply skips the memory.
        if let Ok(memory) = self.matches.try_lock() {
            if let Some(known) = memory.by_pattern.get(pattern).and_then(|answers| answers.get(text)) {
                return *known;
            }
        }
        let matched = regex.is_match(text);
        if let Ok(mut memory) = self.matches.try_lock() {
            if memory.entries >= MAX_REMEMBERED_MATCHES {
                memory.by_pattern.iter_mut().for_each(HashMap::clear);
                memory.entries = 0;
            }
            if memory.by_pattern.len() <= pattern {
                memory.by_pattern.resize_with(pattern + 1, HashMap::new);
            }
            if memory.by_pattern[pattern].insert(text.into(), matched).is_none() {
                memory.entries += 1;
            }
        }
        matched
    }

    fn string_valid(&self, cx: &mut Context<'_>, kw: &Keywords, text: &Bound<'_, PyString>) -> Verdict<bool> {
        if kw.min_length.is_some() || kw.max_length.is_some() {
            let length = string_length(text);
            if kw.min_length.is_some_and(|bound| length < bound) || kw.max_length.is_some_and(|bound| length > bound) {
                return Ok(false);
            }
        }
        if let Some(pattern) = kw.pattern {
            let matched = match &self.patterns[pattern] {
                Pattern::Native(regex) => self.native_match(pattern, regex, text.to_str()?),
                Pattern::Python(compiled) => !compiled.bind(cx.py).call_method1("search", (text,))?.is_none(),
            };
            if !matched {
                return Ok(false);
            }
        }
        if kw.date_time {
            let decided = match text.to_str() {
                Ok(slice) => is_rfc3339_date_time(slice),
                Err(_) => None,
            };
            let fits = match decided {
                Some(fits) => fits,
                None => self.date_time.bind(cx.py).call1((text,))?.is_truthy()?,
            };
            if !fits {
                return Ok(false);
            }
        }
        Ok(true)
    }

    fn array_valid<'py>(&self, cx: &mut Context<'py>, kw: &Keywords, array: &Bound<'py, PyAny>) -> Verdict<bool> {
        let total = array_len(array);
        if kw.min_items.is_some_and(|bound| (total as i64) < bound) || kw.max_items.is_some_and(|bound| (total as i64) > bound) {
            return Ok(false);
        }
        for (index, node) in kw.prefix_items.iter().enumerate().take(total) {
            if !self.valid(cx, *node, &array_item(array, index)?)? {
                return Ok(false);
            }
        }
        if let Some(items) = kw.items {
            let prefix = kw.prefix_items.len();
            if total > prefix {
                match items {
                    ANY => {}
                    NEVER => return Ok(false),
                    _ => {
                        for index in prefix..total {
                            if !self.valid(cx, items, &array_item(array, index)?)? {
                                return Ok(false);
                            }
                        }
                    }
                }
            }
        }
        if kw.unique && !self.unique(cx, array, total)? {
            return Ok(false);
        }
        if let Some(contains) = kw.contains {
            let most = kw.max_contains.unwrap_or(total as i64);
            let mut matches: i64 = 0;
            for index in 0..total {
                if self.valid(cx, contains, &array_item(array, index)?)? {
                    matches += 1;
                    if matches > most {
                        return Ok(false);
                    }
                }
            }
            if matches < kw.min_contains {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// Size bounds, `required`, `dependentRequired` and the light properties. `None` when one
    /// fails, otherwise how many light properties are present.
    fn object_shallow<'py>(&self, cx: &mut Context<'py>, kw: &Keywords, object: &Bound<'py, PyDict>) -> Verdict<Option<i64>> {
        let py = cx.py;
        let total = object.len() as i64;
        if kw.min_properties.is_some_and(|bound| total < bound) || kw.max_properties.is_some_and(|bound| total > bound) {
            return Ok(None);
        }
        for name in &kw.required {
            if !object.contains(name.bind(py))? {
                return Ok(None);
            }
        }
        for (name, dependencies) in &kw.dependent_required {
            if object.contains(name.bind(py))? {
                for dependency in dependencies {
                    if !object.contains(dependency.bind(py))? {
                        return Ok(None);
                    }
                }
            }
        }
        let mut present: i64 = 0;
        for (name, node) in &kw.properties[..kw.light_properties] {
            if let Some(member) = object.get_item(name.bind(py))? {
                present += 1;
                if !self.valid(cx, *node, &member)? {
                    return Ok(None);
                }
            }
        }
        Ok(Some(present))
    }

    /// The heavy properties, `additionalProperties`, `propertyNames`, `unevaluatedProperties`.
    fn object_deep<'py>(&self, cx: &mut Context<'py>, kw: &Keywords, object: &Bound<'py, PyDict>, present: i64) -> Verdict<bool> {
        let py = cx.py;
        let total = object.len() as i64;
        let mut present = present;
        for (name, node) in &kw.properties[kw.light_properties..] {
            if let Some(member) = object.get_item(name.bind(py))? {
                present += 1;
                if !self.valid(cx, *node, &member)? {
                    return Ok(false);
                }
            }
        }
        if let Some(additional) = kw.additional {
            match additional {
                ANY => {}
                // Every present property is a distinct instance key, so any other key is extra.
                NEVER => {
                    if present < total {
                        return Ok(false);
                    }
                }
                _ => {
                    for (key, member) in object.iter() {
                        let declared = match &kw.property_set {
                            Some(set) => set.bind(py).contains(&key)?,
                            None => false,
                        };
                        if !declared && !self.valid(cx, additional, &member)? {
                            return Ok(false);
                        }
                    }
                }
            }
        }
        if let Some(names) = kw.property_names {
            for key in object.keys() {
                if !self.valid(cx, names, &key)? {
                    return Ok(false);
                }
            }
        }
        if let Some(unevaluated) = kw.unevaluated {
            let mut evaluated = HashSet::new();
            self.evaluated_keywords(cx, kw, object, &mut evaluated)?;
            for (key, member) in object.iter() {
                if !evaluated.contains(key_text(&key)?.as_str()) && !self.valid(cx, unevaluated, &member)? {
                    return Ok(false);
                }
            }
        }
        Ok(true)
    }

    fn applicators_valid<'py>(&self, cx: &mut Context<'py>, kw: &Keywords, item: &Bound<'py, PyAny>) -> Verdict<bool> {
        if let Some(reference) = kw.reference {
            if !self.valid(cx, reference, item)? {
                return Ok(false);
            }
        }
        for node in &kw.all_of {
            if !self.valid(cx, *node, item)? {
                return Ok(false);
            }
        }
        if !kw.any_of.is_empty() {
            let mut any = false;
            for node in &kw.any_of {
                if self.valid(cx, *node, item)? {
                    any = true;
                    break;
                }
            }
            if !any {
                return Ok(false);
            }
        }
        if !kw.one_of.is_empty() {
            let mut matched = 0;
            for node in &kw.one_of {
                if self.valid(cx, *node, item)? {
                    matched += 1;
                    if matched > 1 {
                        return Ok(false);
                    }
                }
            }
            if matched != 1 {
                return Ok(false);
            }
        }
        if let Some(not) = kw.not {
            if self.valid(cx, not, item)? {
                return Ok(false);
            }
        }
        if let Some(condition) = kw.condition {
            let branch = if self.valid(cx, condition, item)? { kw.then } else { kw.otherwise };
            if let Some(branch) = branch {
                if !self.valid(cx, branch, item)? {
                    return Ok(false);
                }
            }
        }
        Ok(true)
    }

    /// `find_evaluated_property_keys_by_schema` for one node.
    fn evaluated_node<'py>(
        &self,
        cx: &mut Context<'py>,
        node: NodeId,
        object: &Bound<'py, PyDict>,
        out: &mut HashSet<String>,
    ) -> Verdict<()> {
        match &self.nodes[node as usize] {
            Node::Any | Node::Never => Ok(()),
            Node::Schema(keywords) => {
                if cx.depth >= MAX_APPLICATION_DEPTH {
                    return Err(Defer);
                }
                cx.depth += 1;
                let result = self.evaluated_keywords(cx, keywords, object, out);
                cx.depth -= 1;
                result
            }
        }
    }

    fn evaluated_keywords<'py>(
        &self,
        cx: &mut Context<'py>,
        kw: &Keywords,
        object: &Bound<'py, PyDict>,
        out: &mut HashSet<String>,
    ) -> Verdict<()> {
        let py = cx.py;
        let item = object.as_any();
        if let Some(reference) = kw.reference {
            self.evaluated_node(cx, reference, object, out)?;
        }
        if kw.property_set.is_some() {
            for (name, _) in &kw.properties {
                if object.contains(name.bind(py))? {
                    out.insert(name.bind(py).to_str()?.to_owned());
                }
            }
        }
        for node in [kw.additional, kw.unevaluated].into_iter().flatten() {
            for (key, member) in object.iter() {
                if self.valid(cx, node, &member)? {
                    out.insert(key_text(&key)?);
                }
            }
        }
        for node in kw.all_of.iter().chain(&kw.one_of).chain(&kw.any_of) {
            if self.valid(cx, *node, item)? {
                self.evaluated_node(cx, *node, object, out)?;
            }
        }
        if let Some(condition) = kw.condition {
            if self.valid(cx, condition, item)? {
                self.evaluated_node(cx, condition, object, out)?;
                if let Some(then) = kw.then {
                    self.evaluated_node(cx, then, object, out)?;
                }
            } else if let Some(otherwise) = kw.otherwise {
                self.evaluated_node(cx, otherwise, object, out)?;
            }
        }
        Ok(())
    }

    /// `_utils.uniq` over an array's members.
    fn unique<'py>(&self, cx: &mut Context<'py>, array: &Bound<'py, PyAny>, total: usize) -> Verdict<bool> {
        if total <= 1 {
            return Ok(true);
        }
        let mut members = Vec::with_capacity(total);
        for index in 0..total {
            members.push(cx.value(&array_item(array, index)?)?);
        }
        if members.iter().all(|member| matches!(member, Value::Str(_))) {
            // Sortable: adjacent members after sorting compare with `==`.
            let mut seen = HashSet::with_capacity(total);
            for member in &members {
                if let Value::Str(text) = member {
                    if !seen.insert(text.to_str()?) {
                        return Ok(false);
                    }
                }
            }
            return Ok(true);
        }
        if members.iter().all(|member| matches!(member, Value::Int(_))) {
            let mut seen = HashSet::with_capacity(total);
            for member in &members {
                if let Value::Int(number) = member {
                    if !seen.insert(*number) {
                        return Ok(false);
                    }
                }
            }
            return Ok(true);
        }
        if members.iter().all(|member| matches!(member, Value::Array(_))) {
            // Sorting lists of lists may or may not raise depending on their contents; the
            // comparison order decides which pairs `uniq` checks.
            return Err(Defer);
        }
        // Any `None`, `bool` (unbool sentinel), object, or mix of str/int/array cannot be sorted,
        // so `uniq` compares every pair with `equal`.
        for left in 0..total {
            for right in (left + 1)..total {
                if self.equal_values(cx, &members[left], &members[right])? {
                    return Ok(false);
                }
            }
        }
        Ok(true)
    }

    /// `_utils.equal` between two instance values.
    fn equal_values<'py>(&self, cx: &mut Context<'py>, left: &Value<'py>, right: &Value<'py>) -> Verdict<bool> {
        Ok(match (left, right) {
            (Value::Null, Value::Null) => true,
            (Value::Bool(a), Value::Bool(b)) => a == b,
            (Value::Int(a), Value::Int(b)) => a == b,
            (Value::Str(a), Value::Str(b)) => a.as_any().eq(b)?,
            (Value::Array(a), Value::Array(b)) => {
                let length = array_len(a);
                if length != array_len(b) {
                    return Ok(false);
                }
                for index in 0..length {
                    let x = cx.value(&array_item(a, index)?)?;
                    let y = cx.value(&array_item(b, index)?)?;
                    if !self.equal_values(cx, &x, &y)? {
                        return Ok(false);
                    }
                }
                true
            }
            (Value::Object(a), Value::Object(b)) => {
                if a.len() != b.len() {
                    return Ok(false);
                }
                for (key, member) in a.iter() {
                    let Some(other) = b.get_item(&key)? else {
                        return Ok(false);
                    };
                    let x = cx.value(&member)?;
                    let y = cx.value(&other)?;
                    if !self.equal_values(cx, &x, &y)? {
                        return Ok(false);
                    }
                }
                true
            }
            _ => false,
        })
    }

    /// `_utils.equal` between an instance value and a schema constant.
    fn equals_constant<'py>(&self, cx: &mut Context<'py>, value: &Value<'py>, constant: &Constant) -> Verdict<bool> {
        Ok(match (value, constant) {
            (Value::Null, Constant::Null) => true,
            (Value::Bool(a), Constant::Bool(b)) => a == b,
            (Value::Int(a), Constant::Int(b)) => a == b,
            (Value::Str(a), Constant::Str(b)) => a.to_str()? == b,
            (Value::Array(array), Constant::Array(members)) => {
                if array_len(array) != members.len() {
                    return Ok(false);
                }
                for (index, member) in members.iter().enumerate() {
                    let x = cx.value(&array_item(array, index)?)?;
                    if !self.equals_constant(cx, &x, member)? {
                        return Ok(false);
                    }
                }
                true
            }
            (Value::Object(object), Constant::Object(members)) => {
                if object.len() != members.len() {
                    return Ok(false);
                }
                for (key, member) in members {
                    let Some(other) = object.get_item(key)? else {
                        return Ok(false);
                    };
                    let x = cx.value(&other)?;
                    if !self.equals_constant(cx, &x, member)? {
                        return Ok(false);
                    }
                }
                true
            }
            _ => false,
        })
    }
}

fn number_valid(kw: &Keywords, number: i64) -> bool {
    !(kw.minimum.is_some_and(|bound| number < bound)
        || kw.maximum.is_some_and(|bound| number > bound)
        || kw.exclusive_minimum.is_some_and(|bound| number <= bound)
        || kw.exclusive_maximum.is_some_and(|bound| number >= bound))
}

/// An instance key as text; any other key type is left to Python.
fn key_text(key: &Bound<'_, PyAny>) -> Verdict<String> {
    if !exact(key, ffi::PyUnicode_CheckExact) {
        return Err(Defer);
    }
    Ok(unsafe { key.cast_unchecked::<PyString>() }.to_str()?.to_owned())
}

// ---------------------------------------------------------------------------------------------
// Compilation
// ---------------------------------------------------------------------------------------------

struct Compiler<'py> {
    documents: HashMap<String, Bound<'py, PyDict>>,
    root_addresses: HashSet<usize>,
    nodes: Vec<Node>,
    by_address: HashMap<usize, NodeId>,
    patterns: Vec<Pattern>,
    pattern_ids: HashMap<String, usize>,
    re_compile: Bound<'py, PyAny>,
    /// The keywords the mirrored validator class applies; every other key is an annotation.
    applied: HashSet<String>,
}

fn integer(value: &Bound<'_, PyAny>) -> Result<i64, Refuse> {
    if !exact(value, ffi::PyLong_CheckExact) {
        return Err(Refuse);
    }
    value.extract::<i64>().map_err(|_| Refuse)
}

fn text(value: &Bound<'_, PyAny>) -> Result<String, Refuse> {
    if !exact(value, ffi::PyUnicode_CheckExact) {
        return Err(Refuse);
    }
    Ok(unsafe { value.cast_unchecked::<PyString>() }.to_str()?.to_owned())
}

fn text_object<'py>(value: &Bound<'py, PyAny>) -> Result<Py<PyString>, Refuse> {
    if !exact(value, ffi::PyUnicode_CheckExact) {
        return Err(Refuse);
    }
    let text = unsafe { value.cast_unchecked::<PyString>() };
    text.to_str()?;
    Ok(text.clone().unbind())
}

fn list<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Result<&'a Bound<'py, PyList>, Refuse> {
    if !exact(value, ffi::PyList_CheckExact) {
        return Err(Refuse);
    }
    Ok(unsafe { value.cast_unchecked::<PyList>() })
}

fn dict<'a, 'py>(value: &'a Bound<'py, PyAny>) -> Result<&'a Bound<'py, PyDict>, Refuse> {
    if !exact(value, ffi::PyDict_CheckExact) {
        return Err(Refuse);
    }
    Ok(unsafe { value.cast_unchecked::<PyDict>() })
}

fn constant(value: &Bound<'_, PyAny>, depth: usize) -> Result<Constant, Refuse> {
    if depth > 64 {
        return Err(Refuse);
    }
    if value.is_none() {
        return Ok(Constant::Null);
    }
    if unsafe { ffi::PyBool_Check(value.as_ptr()) } != 0 {
        return Ok(Constant::Bool(value.as_ptr() == unsafe { ffi::Py_True() }));
    }
    if exact(value, ffi::PyLong_CheckExact) {
        return Ok(Constant::Int(integer(value)?));
    }
    if exact(value, ffi::PyUnicode_CheckExact) {
        return Ok(Constant::Str(text(value)?));
    }
    if exact(value, ffi::PyList_CheckExact) {
        let mut members = Vec::new();
        for member in list(value)?.iter() {
            members.push(constant(&member, depth + 1)?);
        }
        return Ok(Constant::Array(members));
    }
    if exact(value, ffi::PyDict_CheckExact) {
        let mut members = Vec::new();
        for (key, member) in dict(value)?.iter() {
            members.push((text(&key)?, constant(&member, depth + 1)?));
        }
        return Ok(Constant::Object(members));
    }
    Err(Refuse)
}

fn type_bit(name: &str) -> Result<u8, Refuse> {
    Ok(match name {
        "null" => TYPE_NULL,
        "boolean" => TYPE_BOOLEAN,
        "integer" => TYPE_INTEGER,
        "number" => TYPE_NUMBER,
        "string" => TYPE_STRING,
        "array" => TYPE_ARRAY,
        "object" => TYPE_OBJECT,
        _ => return Err(Refuse),
    })
}

/// A string value with key `"$id"` marks a schema resource; nested ones change the base URI.
fn declares_id(value: &Bound<'_, PyAny>) -> Result<bool, Refuse> {
    if !exact(value, ffi::PyDict_CheckExact) {
        return Ok(false);
    }
    Ok(match dict(value)?.get_item("$id")? {
        Some(id) => exact(&id, ffi::PyUnicode_CheckExact),
        None => false,
    })
}

impl<'py> Compiler<'py> {
    fn schema(&mut self, value: &Bound<'py, PyAny>, document: &str) -> Result<NodeId, Refuse> {
        if unsafe { ffi::PyBool_Check(value.as_ptr()) } != 0 {
            return Ok(if value.as_ptr() == unsafe { ffi::Py_True() } { ANY } else { NEVER });
        }
        let schema = dict(value)?;
        let address = value.as_ptr() as usize;
        if let Some(node) = self.by_address.get(&address) {
            return Ok(*node);
        }
        let node = NodeId::try_from(self.nodes.len()).map_err(|_| Refuse)?;
        self.nodes.push(Node::Any);
        self.by_address.insert(address, node);
        let mut kw = Keywords { min_contains: 1, ..Keywords::default() };
        let mut applies = false;
        for (key, member) in schema.iter() {
            let key = text(&key)?;
            if key == "$id" {
                if !self.root_addresses.contains(&address) {
                    return Err(Refuse);
                }
                continue;
            }
            if REFUSED_KEYWORDS.contains(&key.as_str()) {
                return Err(Refuse);
            }
            if !self.applied.contains(&key) {
                continue;
            }
            applies = true;
            match key.as_str() {
                "$ref" => {
                    let reference = text(&member)?;
                    let (target, target_document) = self.resolve(document, &reference)?;
                    kw.reference = Some(self.schema(&target, &target_document)?);
                }
                "type" => {
                    let mut bits = 0;
                    if exact(&member, ffi::PyUnicode_CheckExact) {
                        bits = type_bit(&text(&member)?)?;
                    } else {
                        for name in list(&member)?.iter() {
                            bits |= type_bit(&text(&name)?)?;
                        }
                    }
                    kw.types = Some(bits);
                }
                "const" => kw.constant = Some(constant(&member, 0)?),
                "enum" => {
                    let mut choices = Vec::new();
                    for choice in list(&member)?.iter() {
                        choices.push(constant(&choice, 0)?);
                    }
                    kw.choices = Some(choices);
                }
                "minLength" => kw.min_length = Some(integer(&member)?),
                "maxLength" => kw.max_length = Some(integer(&member)?),
                "pattern" => kw.pattern = Some(self.pattern(&text(&member)?)?),
                // The format checker checks `date-time` only; any other format is not checked.
                "format" => kw.date_time = text(&member)? == "date-time",
                "minimum" => kw.minimum = Some(integer(&member)?),
                "maximum" => kw.maximum = Some(integer(&member)?),
                "exclusiveMinimum" => kw.exclusive_minimum = Some(integer(&member)?),
                "exclusiveMaximum" => kw.exclusive_maximum = Some(integer(&member)?),
                "properties" => {
                    let properties = dict(&member)?;
                    for (name, subschema) in properties.iter() {
                        let name = text_object(&name)?;
                        let target = self.schema(&subschema, document)?;
                        kw.properties.push((name, target));
                    }
                    kw.property_set = Some(properties.clone().unbind());
                }
                "required" => {
                    for name in list(&member)?.iter() {
                        kw.required.push(text_object(&name)?);
                    }
                }
                "additionalProperties" => kw.additional = Some(self.schema(&member, document)?),
                "unevaluatedProperties" => kw.unevaluated = Some(self.schema(&member, document)?),
                "minProperties" => kw.min_properties = Some(integer(&member)?),
                "maxProperties" => kw.max_properties = Some(integer(&member)?),
                "dependentRequired" => {
                    for (name, dependencies) in dict(&member)?.iter() {
                        let mut names = Vec::new();
                        for dependency in list(&dependencies)?.iter() {
                            names.push(text_object(&dependency)?);
                        }
                        kw.dependent_required.push((text_object(&name)?, names));
                    }
                }
                "propertyNames" => kw.property_names = Some(self.schema(&member, document)?),
                "prefixItems" => {
                    for subschema in list(&member)?.iter() {
                        let target = self.schema(&subschema, document)?;
                        kw.prefix_items.push(target);
                    }
                }
                "items" => kw.items = Some(self.schema(&member, document)?),
                "minItems" => kw.min_items = Some(integer(&member)?),
                "maxItems" => kw.max_items = Some(integer(&member)?),
                "uniqueItems" => {
                    if unsafe { ffi::PyBool_Check(member.as_ptr()) } == 0 {
                        return Err(Refuse);
                    }
                    kw.unique = member.as_ptr() == unsafe { ffi::Py_True() };
                }
                "contains" => {
                    kw.contains = Some(self.schema(&member, document)?);
                    if let Some(least) = schema.get_item("minContains")? {
                        kw.min_contains = integer(&least)?;
                    }
                    if let Some(most) = schema.get_item("maxContains")? {
                        kw.max_contains = Some(integer(&most)?);
                    }
                }
                "allOf" | "anyOf" | "oneOf" => {
                    let mut targets = Vec::new();
                    for subschema in list(&member)?.iter() {
                        targets.push(self.schema(&subschema, document)?);
                    }
                    match key.as_str() {
                        "allOf" => kw.all_of = targets,
                        "anyOf" => kw.any_of = targets,
                        _ => kw.one_of = targets,
                    }
                }
                "not" => kw.not = Some(self.schema(&member, document)?),
                "if" => {
                    kw.condition = Some(self.schema(&member, document)?);
                    if let Some(then) = schema.get_item("then")? {
                        kw.then = Some(self.schema(&then, document)?);
                    }
                    if let Some(otherwise) = schema.get_item("else")? {
                        kw.otherwise = Some(self.schema(&otherwise, document)?);
                    }
                }
                _ => return Err(Refuse),
            }
        }
        if applies {
            self.nodes[node as usize] = Node::Schema(Box::new(kw));
        }
        Ok(node)
    }

    /// Resolve `reference` from `document` the way `referencing` does for this catalog, or refuse.
    fn resolve(&self, document: &str, reference: &str) -> Result<(Bound<'py, PyAny>, String), Refuse> {
        let (uri, fragment) = match reference.strip_prefix('#') {
            Some(fragment) => (document.to_owned(), fragment),
            None => {
                // `urljoin` leaves an absolute, already-normal catalog URI unchanged.
                let (base, fragment) = reference.split_once('#').unwrap_or((reference, ""));
                if !base.starts_with("https://") || base.contains(['%', '?', '\\']) || base.contains("/.") {
                    return Err(Refuse);
                }
                (base.to_owned(), fragment)
            }
        };
        let root = self.documents.get(&uri).ok_or(Refuse)?;
        let mut current = root.clone().into_any();
        if fragment.is_empty() {
            return Ok((current, uri));
        }
        if !fragment.starts_with('/') || fragment.contains('%') {
            return Err(Refuse);
        }
        for segment in fragment[1..].split('/') {
            current = if exact(&current, ffi::PyList_CheckExact) {
                if segment.is_empty() || !segment.bytes().all(|byte| byte.is_ascii_digit()) {
                    return Err(Refuse);
                }
                let index: usize = segment.parse().map_err(|_| Refuse)?;
                let members = list(&current)?;
                if index >= members.len() {
                    return Err(Refuse);
                }
                members.get_item(index)?
            } else if exact(&current, ffi::PyDict_CheckExact) {
                let key = segment.replace("~1", "/").replace("~0", "~");
                dict(&current)?.get_item(key)?.ok_or(Refuse)?
            } else {
                return Err(Refuse);
            };
            if declares_id(&current)? {
                return Err(Refuse);
            }
        }
        Ok((current, uri))
    }

    fn pattern(&mut self, source: &str) -> Result<usize, Refuse> {
        if let Some(index) = self.pattern_ids.get(source) {
            return Ok(*index);
        }
        // Python must accept the pattern either way, or the stock keyword would raise.
        let compiled = self.re_compile.call1((source,))?;
        let pattern = match native_pattern(source) {
            Some(regex) => Pattern::Native(regex),
            None => Pattern::Python(compiled.unbind()),
        };
        let index = self.patterns.len();
        self.patterns.push(pattern);
        self.pattern_ids.insert(source.to_owned(), index);
        Ok(index)
    }
}

/// Whether a node applies nothing but one `$ref` (annotations aside).
fn bare_reference(node: &Node) -> Option<NodeId> {
    let Node::Schema(kw) = node else {
        return None;
    };
    let reference = kw.reference?;
    (children(kw).count() == 1
        && kw.types.is_none()
        && kw.constant.is_none()
        && kw.choices.is_none()
        && kw.min_length.is_none()
        && kw.max_length.is_none()
        && kw.pattern.is_none()
        && !kw.date_time
        && kw.minimum.is_none()
        && kw.maximum.is_none()
        && kw.exclusive_minimum.is_none()
        && kw.exclusive_maximum.is_none()
        && kw.property_set.is_none()
        && kw.required.is_empty()
        && kw.min_properties.is_none()
        && kw.max_properties.is_none()
        && kw.dependent_required.is_empty()
        && kw.min_items.is_none()
        && kw.max_items.is_none()
        && !kw.unique
        && kw.contains.is_none())
        .then_some(reference)
}

/// Point every use of a node that applies only a `$ref` at the reference's target: applying
/// `{"$ref": X}` decides exactly what applying X decides, including the keys
/// `unevaluatedProperties` counts as evaluated. A reference cycle of bare references is left as
/// is (the walk defers on it by depth, like any cycle).
fn collapse_references(nodes: &mut [Node], roots: HashMap<String, NodeId>) -> HashMap<String, NodeId> {
    let targets: Vec<NodeId> = (0..nodes.len())
        .map(|start| {
            let mut node = start as NodeId;
            for _ in 0..64 {
                match bare_reference(&nodes[node as usize]) {
                    Some(next) if next != start as NodeId => node = next,
                    Some(_) => return start as NodeId,
                    None => return node,
                }
            }
            start as NodeId
        })
        .collect();
    let target = |node: NodeId| targets[node as usize];
    for node in nodes.iter_mut() {
        let Node::Schema(kw) = node else {
            continue;
        };
        for (_, child) in kw.properties.iter_mut() {
            *child = target(*child);
        }
        for slot in [
            &mut kw.additional,
            &mut kw.unevaluated,
            &mut kw.property_names,
            &mut kw.items,
            &mut kw.contains,
            &mut kw.reference,
            &mut kw.not,
            &mut kw.condition,
            &mut kw.then,
            &mut kw.otherwise,
        ]
        .into_iter()
        .flatten()
        {
            *slot = target(*slot);
        }
        for list in [&mut kw.prefix_items, &mut kw.all_of, &mut kw.any_of, &mut kw.one_of] {
            for child in list.iter_mut() {
                *child = target(*child);
            }
        }
    }
    roots.into_iter().map(|(schema_id, node)| (schema_id, target(node))).collect()
}

fn type_kinds(types: u8) -> u8 {
    let mut kinds = 0;
    for (bit, kind) in [
        (TYPE_NULL, KIND_NULL),
        (TYPE_BOOLEAN, KIND_BOOL),
        (TYPE_INTEGER, KIND_INT),
        (TYPE_NUMBER, KIND_INT),
        (TYPE_STRING, KIND_STR),
        (TYPE_ARRAY, KIND_ARRAY),
        (TYPE_OBJECT, KIND_OBJECT),
    ] {
        if types & bit != 0 {
            kinds |= kind;
        }
    }
    kinds
}

/// The only kind of instance `_utils.equal` can find equal to *constant*.
fn constant_kind(constant: &Constant) -> u8 {
    match constant {
        Constant::Null => KIND_NULL,
        Constant::Bool(_) => KIND_BOOL,
        Constant::Int(_) => KIND_INT,
        Constant::Str(_) => KIND_STR,
        Constant::Array(_) => KIND_ARRAY,
        Constant::Object(_) => KIND_OBJECT,
    }
}

/// A superset of the instance kinds a node accepts: an instance of any other kind fails it
/// whatever its value (`type`, `const` and `enum` admit one kind each, a `$ref`/`allOf` member
/// must hold too, an `anyOf`/`oneOf` needs one branch). Nodes on a reference cycle get every
/// kind.
fn accepted_kinds(nodes: &[Node], node: NodeId, memo: &mut [Option<u8>], visiting: &mut [bool]) -> u8 {
    if let Some(known) = memo[node as usize] {
        return known;
    }
    let kw = match &nodes[node as usize] {
        Node::Any => return KIND_ALL,
        Node::Never => return 0,
        Node::Schema(kw) => kw,
    };
    if visiting[node as usize] {
        return KIND_ALL;
    }
    visiting[node as usize] = true;
    let mut kinds = KIND_ALL;
    if let Some(types) = kw.types {
        kinds &= type_kinds(types);
    }
    if let Some(constant) = &kw.constant {
        kinds &= constant_kind(constant);
    }
    if let Some(choices) = &kw.choices {
        kinds &= choices.iter().fold(0, |union, choice| union | constant_kind(choice));
    }
    for child in kw.reference.iter().chain(&kw.all_of) {
        kinds &= accepted_kinds(nodes, *child, memo, visiting);
    }
    for branches in [&kw.any_of, &kw.one_of] {
        if !branches.is_empty() {
            let union = branches.iter().fold(0, |union, child| union | accepted_kinds(nodes, *child, memo, visiting));
            kinds &= union;
        }
    }
    visiting[node as usize] = false;
    memo[node as usize] = Some(kinds);
    kinds
}

/// A property subschema at most this heavy is checked before the node's applicators.
const LIGHT_WEIGHT: u32 = 4;
const CYCLE_WEIGHT: u32 = 1 << 20;

fn children(kw: &Keywords) -> impl Iterator<Item = NodeId> + '_ {
    kw.properties
        .iter()
        .map(|(_, node)| *node)
        .chain(kw.additional)
        .chain(kw.unevaluated)
        .chain(kw.property_names)
        .chain(kw.prefix_items.iter().copied())
        .chain(kw.items)
        .chain(kw.contains)
        .chain(kw.reference)
        .chain(kw.all_of.iter().copied())
        .chain(kw.any_of.iter().copied())
        .chain(kw.one_of.iter().copied())
        .chain(kw.not)
        .chain(kw.condition)
        .chain(kw.then)
        .chain(kw.otherwise)
}

/// How much schema a node can apply: its keyword nodes reachable without repetition, saturating;
/// a node on a reference cycle is as heavy as it gets.
fn weight(nodes: &[Node], node: NodeId, memo: &mut [Option<u32>], visiting: &mut [bool]) -> u32 {
    if let Some(known) = memo[node as usize] {
        return known;
    }
    let Node::Schema(kw) = &nodes[node as usize] else {
        return 0;
    };
    if visiting[node as usize] {
        return CYCLE_WEIGHT;
    }
    visiting[node as usize] = true;
    let mut total: u32 = 1;
    for child in children(kw) {
        total = total.saturating_add(weight(nodes, child, memo, visiting)).min(CYCLE_WEIGHT);
    }
    visiting[node as usize] = false;
    memo[node as usize] = Some(total);
    total
}

/// Order every node's properties cheapest first and mark the light ones. Validity does not
/// depend on the order in which `properties` members are checked.
fn order_properties(nodes: &mut [Node]) {
    let mut memo = vec![None; nodes.len()];
    let mut visiting = vec![false; nodes.len()];
    let weights: Vec<u32> = (0..nodes.len()).map(|node| weight(nodes, node as NodeId, &mut memo, &mut visiting)).collect();
    for node in nodes.iter_mut() {
        if let Node::Schema(kw) = node {
            kw.properties.sort_by_key(|(_, child)| weights[*child as usize]);
            kw.light_properties = kw.properties.iter().take_while(|(_, child)| weights[*child as usize] <= LIGHT_WEIGHT).count();
        }
    }
}

/// Translated patterns by source, shared by every compile in the process (a `Regex` clone shares
/// its compiled program), so rebuilding a checker does not recompile the catalog's patterns.
static NATIVE_PATTERNS: Mutex<Option<HashMap<String, Option<Regex>>>> = Mutex::new(None);

fn native_pattern(source: &str) -> Option<Regex> {
    let mut cache = NATIVE_PATTERNS.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    let cache = cache.get_or_insert_with(HashMap::new);
    if let Some(known) = cache.get(source) {
        return known.clone();
    }
    let compiled = compile_python_pattern(source);
    cache.insert(source.to_owned(), compiled.clone());
    compiled
}

fn compile(
    documents: &Bound<'_, PyDict>,
    re_compile: &Bound<'_, PyAny>,
    date_time: &Bound<'_, PyAny>,
    keywords: &Bound<'_, PyAny>,
) -> Result<SchemaValidity, Refuse> {
    let mut applied = HashSet::new();
    for keyword in keywords.try_iter()? {
        let keyword = text(&keyword?)?;
        if !KNOWN_KEYWORDS.contains(&keyword.as_str()) {
            return Err(Refuse);
        }
        applied.insert(keyword);
    }
    let mut compiler = Compiler {
        documents: HashMap::new(),
        root_addresses: HashSet::new(),
        nodes: vec![Node::Any, Node::Never],
        by_address: HashMap::new(),
        patterns: Vec::new(),
        pattern_ids: HashMap::new(),
        re_compile: re_compile.clone(),
        applied,
    };
    let mut ids = Vec::new();
    for (schema_id, document) in documents.iter() {
        let schema_id = text(&schema_id)?;
        let document = dict(&document)?.clone();
        // The root's own `$id` must name it, or relative references would resolve elsewhere.
        match document.get_item("$id")? {
            Some(id) if text(&id)? == schema_id => {}
            _ => return Err(Refuse),
        }
        compiler.root_addresses.insert(document.as_ptr() as usize);
        compiler.documents.insert(schema_id.clone(), document);
        ids.push(schema_id);
    }
    let mut roots = HashMap::new();
    for schema_id in ids {
        let document = compiler.documents[&schema_id].clone().into_any();
        let node = compiler.schema(&document, &schema_id)?;
        roots.insert(schema_id, node);
    }
    let roots = collapse_references(&mut compiler.nodes, roots);
    order_properties(&mut compiler.nodes);
    let mut memo = vec![None; compiler.nodes.len()];
    let mut visiting = vec![false; compiler.nodes.len()];
    let kinds = (0..compiler.nodes.len())
        .map(|node| accepted_kinds(&compiler.nodes, node as NodeId, &mut memo, &mut visiting))
        .collect();
    Ok(SchemaValidity {
        nodes: compiler.nodes,
        roots,
        patterns: compiler.patterns,
        kinds,
        date_time: date_time.clone().unbind(),
        matches: Mutex::new(MatchMemory::default()),
    })
}

/// `compile_schema_validity(documents, re_compile, date_time, keywords) -> SchemaValidity | None`.
///
/// *documents* maps each schema id to its plain document (without `$schema`); *re_compile* is
/// `re.compile`, *date_time* the format checker's `date-time` function, and *keywords* the
/// keywords the mirrored validator class applies. `None` means the catalog uses something the
/// native checker does not reproduce.
#[pyfunction]
pub fn compile_schema_validity(
    documents: &Bound<'_, PyDict>,
    re_compile: &Bound<'_, PyAny>,
    date_time: &Bound<'_, PyAny>,
    keywords: &Bound<'_, PyAny>,
) -> Option<SchemaValidity> {
    compile(documents, re_compile, date_time, keywords).ok()
}

#[pymethods]
impl SchemaValidity {
    /// The stock validator's verdict on *instance* against the schema *schema_id*, or `None`.
    ///
    /// Without *raw*, only exact `dict`/`list`/`str`/`int`/`bool`/`None` values are judged. With
    /// *raw*, *instance* is the canonical value before `_plain_validation_instance`: any mapping
    /// is read through `.items()` as an object and a tuple is an array, and the whole value is
    /// first checked to hold nothing else (a spliced fragment answers `None`). Only then, when
    /// *seen* is given, `seen(key)` may answer `True` from the caller's verdict memory.
    #[pyo3(signature = (schema_id, instance, raw = false, seen = None, key = None))]
    fn check(
        &self,
        py: Python<'_>,
        schema_id: &str,
        instance: &Bound<'_, PyAny>,
        raw: bool,
        seen: Option<&Bound<'_, PyAny>>,
        key: Option<&Bound<'_, PyAny>>,
    ) -> Option<bool> {
        let root = *self.roots.get(schema_id)?;
        let mut cx = Context { py, raw, depth: 0, converted: HashMap::new() };
        if raw {
            cx.ensure_plain(instance, 0).ok()?;
            if let (Some(seen), Some(key)) = (seen, key) {
                if seen.call1((key,)).ok()?.is_truthy().ok()? {
                    return Some(true);
                }
            }
        }
        self.valid(&mut cx, root, instance).ok()
    }

    /// Whether `re.search(pattern, text)` is decided natively, and its answer (tests only).
    #[staticmethod]
    fn pattern_search(pattern: &str, text: &str) -> Option<bool> {
        compile_python_pattern(pattern).map(|regex| regex.is_match(text))
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<SchemaValidity>()?;
    module.add_function(wrap_pyfunction!(compile_schema_validity, module)?)?;
    Ok(())
}
