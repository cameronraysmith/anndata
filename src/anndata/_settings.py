from __future__ import annotations

import os
import textwrap
import warnings
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from enum import Enum
from inspect import Parameter, signature
from typing import TYPE_CHECKING, NamedTuple, TypeVar

from anndata.compat.exceptiongroups import add_note

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Any

T = TypeVar("T")


class DeprecatedOption(NamedTuple):
    option: str
    message: str | None
    removal_version: str | None


# TODO: inherit from Generic[T] as well after python 3.9 is no longer supported
class RegisteredOption(NamedTuple):
    option: str
    default_value: T
    doc: str
    validate: Callable[[T], bool] | None
    type: object


def check_and_get_environ_var(
    key: str,
    default_value: str,
    allowed_values: Sequence[str] | None = None,
    cast: Callable[[Any], T] | type[Enum] = lambda x: x,
) -> T:
    """Get the environment variable and return it is a (potentially) non-string, usable value.

    Parameters
    ----------
    key
        The environment variable name.
    default_value
        The default value for `os.environ.get`.
    allowed_values
        Allowable string values., by default None
    cast
        Casting from the string to a (potentially different) python object, by default lambdax:x

    Returns
    -------
    The casted value.
    """
    environ_value_or_default_value = os.environ.get(key, default_value)
    if (
        allowed_values is not None
        and environ_value_or_default_value not in allowed_values
    ):
        warnings.warn(
            f'Value "{environ_value_or_default_value}" is not in allowed {allowed_values} for environment variable {key}.\
                      Default {default_value} will be used.'
        )
        environ_value_or_default_value = default_value
    return (
        cast(environ_value_or_default_value)
        if not isinstance(cast, type(Enum))
        else cast[environ_value_or_default_value]
    )


def check_and_get_bool(option, default_value):
    return check_and_get_environ_var(
        "ANNDATA_" + option.upper(),
        str(int(default_value)),
        ["0", "1"],
        lambda x: bool(int(x)),
    )


_docstring = """
This manager allows users to customize settings for the anndata package.
Settings here will generally be for advanced use-cases and should be used with caution.

The following options are available:

{options_description}

For setting an option please use :func:`~anndata.settings.override` (local) or set the above attributes directly (global) i.e., `anndata.settings.my_setting = foo`.
For assignment by environment variable, use the variable name in all caps with `ANNDATA_` as the prefix before import of :mod:`anndata`.
For boolean environment variable setting, use 1 for `True` and 0 for `False`.
"""


@dataclass
class SettingsManager:
    _registered_options: dict[str, RegisteredOption] = field(default_factory=dict)
    _deprecated_options: dict[str, DeprecatedOption] = field(default_factory=dict)
    _config: dict[str, object] = field(default_factory=dict)
    __doc_tmpl__: str = _docstring

    def describe(
        self,
        option: str | Iterable[str] | None = None,
        *,
        print_description: bool = True,
    ) -> str:
        """Print and/or return a (string) description of the option(s).

        Parameters
        ----------
        option
            Option(s) to be described, by default None (i.e., do all option)
        print_description
            Whether or not to print the description in addition to returning it., by default True

        Returns
        -------
        The description.
        """
        if option is None:
            return self.describe(
                self._registered_options.keys(), print_description=print_description
            )
        if isinstance(option, Iterable) and not isinstance(option, str):
            return "\n".join(
                [self.describe(k, print_description=print_description) for k in option]
            )
        registered_option = self._registered_options[option]
        doc = registered_option.doc.rstrip("\n")
        if option in self._deprecated_options:
            opt = self._deprecated_options[option]
            if opt.message is not None:
                doc += " *" + opt.message
            doc += f" {option} will be removed in {opt.removal_version}.*"
        if print_description:
            print(doc)
        return doc

    def deprecate(
        self, option: str, removal_version: str, message: str | None = None
    ) -> None:
        """Deprecate options with a message at a version.

        Parameters
        ----------
        option
            Which option should be deprecated.
        removal_version
            The version targeted for removal.
        message
            A custom message.
        """
        self._deprecated_options[option] = DeprecatedOption(
            option, message, removal_version
        )

    def register(
        self,
        option: str,
        default_value: T,
        description: str,
        validate: Callable[[T], bool],
        option_type: object | None = None,
        get_from_env: Callable[[str, T], T] = lambda x, y: y,
    ) -> None:
        """Register an option so it can be set/described etc. by end-users

        Parameters
        ----------
        option
            Option to be set.
        default_value
            Default value with which to set the option.
        description
            Description to be used in the docstring.
        validate
            A function which returns True if the option's value is valid and otherwise should raise a `ValueError` or `TypeError`.
        option
            Optional override for the option type to be displayed.  Otherwise `type(default_value)`.
        get_from_env
            An optional function which takes as arguments the name of the option and a default value and returns the value from the environment variable `ANNDATA_CAPS_OPTION` (or default if not present).
            Default behavior is to return `default_value` without checking the environment.
        """
        try:
            validate(default_value)
        except (ValueError, TypeError) as e:
            add_note(e, f"for option {repr(option)}")
            raise e
        option_type_str = (
            type(default_value).__name__ if option_type is None else str(option_type)
        )
        option_type = type(default_value) if option_type is None else option_type
        doc = f"""\
        {option}: {option_type_str}
            {description} Default value of {default_value}.
        """
        doc = textwrap.dedent(doc)
        self._registered_options[option] = RegisteredOption(
            option, default_value, doc, validate, option_type
        )
        self._config[option] = get_from_env(option, default_value)
        self._update_override_function_for_new_option(option)

    def _update_override_function_for_new_option(
        self,
        option: str,
    ):
        """This function updates the keyword arguments, docstring, and annotations of the `SettingsManager.override` function as the `SettingsManager.register` method is called.

        Parameters
        ----------
        option
            The option being registered for which the override function needs updating.
        """
        option_type = self._registered_options[option].type
        # Update annotations for type checking.
        self.override.__annotations__[option] = option_type
        # __signature__ needs to be updated for tab autocompletion in IPython.
        # See https://github.com/ipython/ipython/issues/11624 for inspiration.
        self.override.__func__.__signature__ = signature(self.override).replace(
            parameters=[
                Parameter(name="self", kind=Parameter.POSITIONAL_ONLY),
                *[
                    Parameter(
                        name=k,
                        annotation=option_type,
                        kind=Parameter.KEYWORD_ONLY,
                    )
                    for k in self._registered_options
                ],
            ]
        )
        # Update docstring for `SettingsManager.override` as well.
        insert_index = self.override.__doc__.find("\n        Yields")
        option_docstring = "\t" + "\t".join(
            self.describe(option, print_description=False).splitlines(keepends=True)
        )
        self.override.__func__.__doc__ = (
            self.override.__doc__[:insert_index]
            + "\n"
            + option_docstring
            + self.override.__doc__[insert_index:]
        )

    def __setattr__(self, option: str, val: object) -> None:
        """
        Set an option to a value.  To see the allowed option to be set and their description,
        use describe_option.

        Parameters
        ----------
        option
            Option to be set.
        val
            Value with which to set the option.

        Raises
        ------
        AttributeError
            If the option has not been registered, this function will raise an error.
        """
        if option in {f.name for f in fields(self)}:
            return super().__setattr__(option, val)
        elif option not in self._registered_options:
            raise AttributeError(
                f"{option} is not an available option for anndata.\
                Please open an issue if you believe this is a mistake."
            )
        registered_option = self._registered_options[option]
        registered_option.validate(val)
        self._config[option] = val

    def __getattr__(self, option: str) -> object:
        """
        Gets the option's value.

        Parameters
        ----------
        option
            Option to be got.

        Returns
        -------
        Value of the option.
        """
        if option in self._deprecated_options:
            deprecated = self._deprecated_options[option]
            warnings.warn(
                DeprecationWarning(
                    f"{repr(option)} will be removed in {deprecated.removal_version}. "
                    + deprecated.message
                )
            )
        if option in self._config:
            return self._config[option]
        raise AttributeError(f"{option} not found.")

    def __dir__(self) -> Iterable[str]:
        return sorted((*dir(super()), *self._config.keys()))

    def reset(self, option: Iterable[str] | str) -> None:
        """
        Resets option(s) to its (their) default value(s).

        Parameters
        ----------
        option
            The option(s) to be reset.
        """
        if isinstance(option, Iterable) and not isinstance(option, str):
            for opt in option:
                self.reset(opt)
        else:
            self._config[option] = self._registered_options[option].default_value

    @contextmanager
    def override(self, **overrides):
        """
        Provides local override via keyword arguments as a context manager.

        Parameters
        ----------

        Yields
        ------
        None
        """
        restore = {a: getattr(self, a) for a in overrides}
        try:
            for attr, value in overrides.items():
                setattr(self, attr, value)
            yield None
        finally:
            for attr, value in restore.items():
                setattr(self, attr, value)

    @property
    def __doc__(self):
        options_description = self.describe(print_description=False)
        return self.__doc_tmpl__.format(
            options_description=options_description,
        )


settings = SettingsManager()

##################################################################################
# PLACE REGISTERED SETTINGS HERE SO THEY CAN BE PICKED UP FOR DOCSTRING CREATION #
##################################################################################


categories_option = "should_remove_unused_categories"
categories_default_value = True
categories_description = (
    "Whether or not to remove unused categories with :class:`~pandas.Categorical`."
)

uniqueness_option = "should_check_uniqueness"
uniqueness_default_value = True
uniqueness_description = "Whether or not to check uniqueness of the `obs` indices on `__init__` of :class:`~anndata.AnnData`."


def validate_bool(val) -> bool:
    if not isinstance(val, bool):
        raise TypeError(f"{val} not valid boolean")
    return True


settings.register(
    categories_option,
    categories_default_value,
    categories_description,
    validate_bool,
    get_from_env=check_and_get_bool,
)

settings.register(
    uniqueness_option,
    uniqueness_default_value,
    uniqueness_description,
    validate_bool,
    get_from_env=check_and_get_bool,
)

##################################################################################
##################################################################################
