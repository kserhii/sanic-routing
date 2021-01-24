import typing as t
from abc import ABC, abstractmethod
from functools import lru_cache
from itertools import count

from .exceptions import NotFound
from .line import Line
from .route import Route
from .tree import Tree
from .utils import parse_parameter_basket

TMP = count()


class BaseRouter(ABC):
    DEFAULT_METHOD = "BASE"
    ALLOWED_METHODS: t.Tuple[str, ...] = tuple()

    def __init__(
        self,
        delimiter: str = "/",
        exception: t.Type[Exception] = NotFound,
        method_handler_exception: t.Type[Exception] = NotFound,
    ) -> None:
        self.static_routes: t.Dict[t.Union[str, t.Tuple[str, ...]], Route] = {}
        self.dynamic_routes: t.Dict[
            t.Union[str, t.Tuple[str, ...]], Route
        ] = {}
        self.delimiter = delimiter
        self.exception = exception
        self.method_handler_exception = method_handler_exception
        self.tree = Tree()
        self.finalized = False

    @abstractmethod
    def get(self):
        ...

    @lru_cache
    def resolve(self, path: str, *, method: t.Optional[str] = None):
        parts = tuple(path[1:].split(self.delimiter))
        route, param_basket = self.find_route(parts, self, {})
        args = []
        params = {}
        handler = None

        if route.static:
            raw_path = path
        else:
            try:
                params, raw_path = parse_parameter_basket(route, param_basket)
            except ValueError:
                raise self.exception
            if method:
                args.append(method)

        handler = route.get_handler(raw_path, method)
        return route, handler, args, params

    def add(
        self,
        path: str,
        handler: t.Callable,
        methods: t.Optional[t.Union[t.List[str], str]] = None,
        name: t.Optional[str] = None,
        requirements: t.Optional[t.Dict[str, t.Any]] = None,
    ) -> None:
        if not methods:
            methods = [self.DEFAULT_METHOD]

        if not isinstance(methods, (list, tuple, set)):
            methods = [methods]

        if self.ALLOWED_METHODS and any(
            method not in self.ALLOWED_METHODS for method in methods
        ):
            # TODO:
            # - Better exception
            raise Exception("bad method")

        if self.finalized:
            # TODO:
            # - Better exception
            raise Exception("finalized")

        static = "<" not in path
        routes = self.static_routes if static else self.dynamic_routes

        path = path.strip(self.delimiter)
        route = Route(self, path, name, requirements)

        if route.parts in routes:
            route = routes[route.parts]
        else:
            routes[route.parts] = route
            if name:
                routes[name] = route

        for method in methods:
            route.add_handler(path, handler, method)

    def finalize(self, do_compile: bool = True):
        self.finalized = True
        self._generate_tree()
        self._render(do_compile)
        for route in self.dynamic_routes.values():
            route.finalize_params()

    def _generate_tree(self) -> None:
        self.tree.generate(self.dynamic_routes)
        self.tree.finalize()

    def _render(self, do_compile: bool = True) -> None:
        src = [
            Line("def find_route(parts, router, basket):", 0),
        ]

        if self.static_routes:
            # src += [
            #     Line("try:", 1),
            #     Line("return router.static_routes[path], None", 2),
            #     Line("except KeyError:", 1),
            #     Line("pass", 2),
            # ]
            src += [
                Line("if parts in router.static_routes:", 1),
                Line("return router.static_routes[parts], None", 2),
            ]
            # src += [
            #     Line("if path in router.static_routes:", 1),
            #     Line("return router.static_routes.get(path), None", 2),
            # ]

        if self.dynamic_routes:
            # src += [Line("parts = path.split(router.delimiter)", 1)]
            src += [Line("num = len(parts)", 1)]
            src += self.tree.render()

        self.optimize(src)

        self.find_route_src = "\n".join(
            map(str, filter(lambda x: x.render, src))
        )
        if do_compile:
            compiled_src = compile(
                self.find_route_src,
                "",
                "exec",
            )
            ctx: t.Dict[
                t.Any, t.Any
            ] = {}  # "REGEX_TYPES": {k: v[1] for k, v in REGEX_TYPES.items()}}
            exec(compiled_src, None, ctx)
            self.find_route = ctx["find_route"]

    @staticmethod
    def optimize(src: t.List[Line]) -> None:
        """
        Insert NotFound exceptions to be able to bail as quick as possible
        """
        offset = 0
        current = 0
        insert_at = set()
        for num, line in enumerate(src):
            if line.indent < current:
                if not line.src.startswith("."):
                    offset = 0

            if (
                line.src.startswith("if")
                or line.src.startswith("elif")
                or line.src.startswith("return")
                or line.src.startswith("basket")
            ):

                idnt = line.indent + 1
                prev_line = src[num - 1]
                while idnt < prev_line.indent:
                    insert_at.add((num, idnt))
                    idnt += 1

            offset += line.offset
            line.indent += offset
            current = line.indent

        idnt = 1
        prev_line = src[-1]
        while idnt < prev_line.indent:
            insert_at.add((len(src), idnt))
            idnt += 1

        for num, indent in sorted(insert_at, key=lambda x: (x[0] * -1, x[1])):
            # TODO:
            # - Proper exception message needed
            i = next(TMP)
            src.insert(num, Line(f"raise NotFound('{indent}//{i}')", indent))
